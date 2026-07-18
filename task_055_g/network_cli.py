"""Task 055-G dynamic network-state CLI.

All commands default to offline operation.  A configuration file cannot grant
network access; only the explicit ``--allow-network`` flag plus the matching
sealed plan hash can do so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from task_055_f.transport import CANONICAL_ORIGIN

from .network_state import (
    Task055GNetworkStateError,
    apply_l1,
    apply_l2,
    build_l2_plan,
    consolidate,
    execute_l1_canary,
    execute_l1_resume,
    execute_l2_canary,
    execute_l2_resume,
    final_verify,
    next_round,
    run_until_blocked,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = _load_config(args.config)
        result = _dispatch(args, config)
    except Task055GNetworkStateError as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _dispatch(args: argparse.Namespace, config: Mapping[str, Any]) -> dict[str, Any]:
    command = str(args.command)
    if command in {"l1-canary", "l1-resume", "l2-canary", "l2-resume"}:
        raise Task055GNetworkStateError("superseded_by_task055j")
    state_root = _required(config, "state_root")
    if command == "consolidate":
        return consolidate(
            state_root=state_root,
            plan_manifest=_required(config, "plan_manifest"),
            execution_manifests=_paths(config.get("execution_manifests")),
        )
    if command == "l1-apply":
        return apply_l1(
            state_root=state_root,
            consolidation_manifest=_required(config, "consolidation_manifest"),
        )
    if command == "l1-canary":
        executor = _build_secure_executor(config, state_root) if args.allow_network else None
        return execute_l1_canary(
            state_root=state_root,
            plan_manifest=_required(config, "plan_manifest"),
            allow_network=bool(args.allow_network),
            sealed_plan_hash=args.sealed_plan_hash,
            request_executor=executor,
        )
    if command == "l1-resume":
        executor = _build_secure_executor(config, state_root) if args.allow_network else None
        return execute_l1_resume(
            state_root=state_root,
            plan_manifest=_required(config, "plan_manifest"),
            canary_manifest=_required(config, "canary_manifest"),
            allow_network=bool(args.allow_network),
            sealed_plan_hash=args.sealed_plan_hash,
            request_executor=executor,
        )
    if command == "l2-plan":
        return build_l2_plan(
            state_root=state_root,
            l1_apply_manifest=_required(config, "l1_apply_manifest"),
            truth_manifest=_required(config, "truth_manifest"),
            frontier_manifest=_required(config, "frontier_manifest"),
        )
    if command == "l2-canary":
        executor = _build_secure_executor(config, state_root) if args.allow_network else None
        return execute_l2_canary(
            state_root=state_root,
            plan_manifest=_required(config, "plan_manifest"),
            allow_network=bool(args.allow_network),
            sealed_plan_hash=args.sealed_plan_hash,
            request_executor=executor,
        )
    if command == "l2-resume":
        executor = _build_secure_executor(config, state_root) if args.allow_network else None
        return execute_l2_resume(
            state_root=state_root,
            plan_manifest=_required(config, "plan_manifest"),
            canary_manifest=_required(config, "canary_manifest"),
            allow_network=bool(args.allow_network),
            sealed_plan_hash=args.sealed_plan_hash,
            request_executor=executor,
        )
    if command == "l2-apply":
        return apply_l2(
            state_root=state_root,
            plan_manifest=_required(config, "plan_manifest"),
            execution_manifests=_paths(config.get("execution_manifests")),
        )
    if command == "next-round":
        return next_round(
            state_root=state_root,
            parent_apply_manifest=_required(config, "parent_apply_manifest"),
            truth_manifest=_required(config, "truth_manifest"),
            frontier_manifest=_required(config, "frontier_manifest"),
        )
    if command == "run-until-blocked":
        return run_until_blocked(
            state_root=state_root,
            plan_manifest=config.get("plan_manifest"),
            execution_manifests=_paths(config.get("execution_manifests")),
        )
    if command == "final-verify":
        return final_verify(state_root=state_root)
    raise Task055GNetworkStateError(f"unknown_network_state_command:{command}")


def _build_secure_executor(
    config: Mapping[str, Any], state_root: str | Path
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Create one credential-bound executor after a strict TLS preflight.

    This function is never called by the default CLI path.  It intentionally
    loads the credential once and passes the same object to every request.
    """

    from data_pipeline.ashare.security import tls_preflight
    from task_055_f.network import _execute_one, load_credential_once

    tls = dict(tls_preflight())
    if (
        tls.get("status") != "passed"
        or tls.get("origin") != CANONICAL_ORIGIN
        or tls.get("hostname_verified") is not True
        or tls.get("certificate_verified") is not True
    ):
        raise Task055GNetworkStateError("network_tls_preflight_failed")
    repo_root = Path(_required(config, "repo_root")).resolve()
    governed_root = Path(_required(config, "governed_data_root")).resolve()
    output_root = Path(_required(config, "output_root")).resolve()
    cache_root = Path(_required(config, "cache_data_root")).resolve()
    credential = load_credential_once(
        repo_root=repo_root,
        governed_root=governed_root,
        output_root=output_root,
    )
    transport_spend = Path(state_root) / "transport_spend"

    def execute(request: Mapping[str, Any]) -> Mapping[str, Any]:
        return _execute_one(
            request=request,
            cache_data_root=cache_root,
            credential=credential,
            client_factory=None,
            spend_root=transport_spend,
        )

    return execute


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Task 055-G immutable dynamic network state machine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "consolidate",
        "l1-apply",
        "l2-plan",
        "l2-apply",
        "next-round",
        "run-until-blocked",
        "final-verify",
    ):
        child = subparsers.add_parser(command)
        child.add_argument("--config", required=True)
    for command in ("l1-canary", "l1-resume", "l2-canary", "l2-resume"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", required=True)
        child.add_argument("--allow-network", action="store_true")
        child.add_argument("--sealed-plan-hash")
    return parser


def _load_config(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Task055GNetworkStateError("network_cli_config_not_object")
    return payload


def _required(config: Mapping[str, Any], key: str) -> Any:
    value = config.get(key)
    if value is None or value == "":
        raise Task055GNetworkStateError(f"network_cli_config_missing:{key}")
    return value


def _paths(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise Task055GNetworkStateError("execution_manifests_must_be_list")
    return tuple(value)


if __name__ == "__main__":
    raise SystemExit(main())

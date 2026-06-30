"""CLI for local production orchestration."""

from __future__ import annotations

import argparse
import json

from .models import ProductionRunMode
from .runner import ProductionOrchestratorConfig, ProductionOrchestratorRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local production orchestration.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan-day", "run-day", "run-phase", "resume", "close-day", "show-run", "show-gates", "package-day", "smoke"]:
        cmd = sub.add_parser(name)
        _add_common(cmd)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--production-state-dir", required=True)
    parser.add_argument("--production-run-id")
    parser.add_argument("--run-mode", choices=[ProductionRunMode.dry_run, ProductionRunMode.shadow_only, ProductionRunMode.paper_simulated, ProductionRunMode.file_outbox], default=ProductionRunMode.shadow_only)
    parser.add_argument("--environment", default="paper")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--require-active-model", action="store_true")
    parser.add_argument("--require-active-optimizer-policy", action="store_true")
    parser.add_argument("--certified-portfolio-policy-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--require-certified-portfolio-policy", action="store_true")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--orders-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--shadow-dir")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-adapter", choices=["paper", "simulated", "file"], default="paper")
    parser.add_argument("--broker-file-gateway", action="store_true")
    parser.add_argument("--broker-file-profile", default="generic_broker_csv")
    parser.add_argument("--broker-file-profile-config")
    parser.add_argument("--broker-file-gateway-store-dir")
    parser.add_argument("--broker-file-outbox-dir")
    parser.add_argument("--broker-file-inbox-dir")
    parser.add_argument("--broker-file-handoff-dir")
    parser.add_argument("--operator-handoff-store-dir")
    parser.add_argument("--operator-handoff-approval-store-dir")
    parser.add_argument("--mapping-certification-decision-path")
    parser.add_argument("--require-mapping-certification", action="store_true")
    parser.add_argument("--file-outbox-dry-run", action="store_true")
    parser.add_argument("--auto-confirm-local-smoke", action="store_true")
    parser.add_argument("--broker-statement-dir")
    parser.add_argument("--eod-reconciliation-dir")
    parser.add_argument("--risk-control-state-dir")
    parser.add_argument("--risk-control-output-dir")
    parser.add_argument("--risk-policy-path")
    parser.add_argument("--incident-store-dir")
    parser.add_argument("--monitoring-dir")
    parser.add_argument("--portfolio-value", type=float, default=1_000_000.0)
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--apply-corporate-actions", action="store_true")
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--target-return-mode", default="adjusted_close")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--require-order-approval", action="store_true")
    parser.add_argument("--approval-id")
    parser.add_argument("--stop-after-phase")
    parser.add_argument("--start-at-phase")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fail-on-blocker", action="store_true")
    parser.add_argument("--continue-on-warning", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = ProductionOrchestratorConfig(**{field: getattr(args, field) for field in ProductionOrchestratorConfig.__dataclass_fields__ if hasattr(args, field)})
    runner = ProductionOrchestratorRunner(cfg)
    if args.command == "plan-day":
        payload = runner.plan_day()
    elif args.command in {"run-day", "run-phase", "smoke"}:
        payload = runner.run_day()
    elif args.command == "resume":
        cfg.resume = True
        payload = runner.run_day()
    elif args.command == "close-day":
        payload = runner.close_day()
    elif args.command in {"show-run", "package-day"}:
        payload = runner.plan_day()
    elif args.command == "show-gates":
        payload = runner.readiness.to_dict()
    else:
        payload = {"status": "failed", "error": f"unsupported command: {args.command}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    failed = payload.get("status") in {"failed"} or (payload.get("status") == "blocked" and args.fail_on_blocker)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

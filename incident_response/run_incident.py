"""CLI for local incident response."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .detectors import detect_incidents
from .models import IncidentRecord, IncidentSeverity, IncidentSource, IncidentStatus
from .report import write_incident_report
from .runbook import build_runbook_steps
from .store import LocalIncidentStore, utc_now


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local production incidents.")
    parser.add_argument("--incident-store-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["detect", "create", "list", "show", "acknowledge", "resolve", "suppress", "report", "smoke"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--production-run-id")
        cmd.add_argument("--trade-date")
        cmd.add_argument("--source", default=IncidentSource.manual)
        cmd.add_argument("--severity", default=IncidentSeverity.warning)
        cmd.add_argument("--code", default="manual_incident")
        cmd.add_argument("--title", default="Manual Incident")
        cmd.add_argument("--description", default="")
        cmd.add_argument("--artifact-dir", action="append", default=[])
        cmd.add_argument("--monitoring-report-path")
        cmd.add_argument("--production-orchestrator-report-path")
        cmd.add_argument("--risk-control-report-path")
        cmd.add_argument("--eod-reconciliation-report-path")
        cmd.add_argument("--freeze-validation-report-path")
        cmd.add_argument("--portfolio-certification-decision-path")
        cmd.add_argument("--incident-id")
        cmd.add_argument("--actor")
        cmd.add_argument("--comment")
        cmd.add_argument("--auto-activate-kill-switch-for-critical", action="store_true")
        cmd.add_argument("--risk-control-state-dir")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = LocalIncidentStore(args.incident_store_dir)
    try:
        if args.command in {"detect", "smoke"}:
            incidents = detect_incidents(store, args.production_run_id, args.trade_date, _artifact_paths(args))
            paths = write_incident_report(store, production_run_id=args.production_run_id, trade_date=args.trade_date)
            payload = {"status": "success", "incident_count": len(incidents), "incidents": [item.to_dict() for item in incidents], "paths": paths}
        elif args.command == "create":
            incident_id = store.make_incident_id(args.production_run_id, args.code, {})
            incident = IncidentRecord(
                incident_id=incident_id,
                production_run_id=args.production_run_id,
                trade_date=args.trade_date,
                severity=args.severity,
                status=IncidentStatus.open,
                source=args.source,
                code=args.code,
                title=args.title,
                description=args.description,
                created_at=utc_now(),
                recommended_actions=["inspect_artifact", "stop_next_phase"],
                runbook_steps=build_runbook_steps(args.code),
            )
            payload = store.save_incident(incident).to_dict()
            write_incident_report(store, production_run_id=args.production_run_id, trade_date=args.trade_date)
        elif args.command == "list":
            incidents = store.list_incidents()
            payload = {"incidents": [item.to_dict() for item in incidents], "count": len(incidents)}
        elif args.command == "show":
            if not args.incident_id:
                raise ValueError("incident-id is required")
            item = store.get_incident(args.incident_id)
            payload = item.to_dict() if item else {}
        elif args.command == "acknowledge":
            payload = store.update_status(_required_incident_id(args), IncidentStatus.acknowledged, args.actor, args.comment).to_dict()
        elif args.command == "resolve":
            payload = store.update_status(_required_incident_id(args), IncidentStatus.resolved, args.actor, args.comment).to_dict()
        elif args.command == "suppress":
            payload = store.update_status(_required_incident_id(args), IncidentStatus.suppressed, args.actor, args.comment).to_dict()
        elif args.command == "report":
            paths = write_incident_report(store, production_run_id=args.production_run_id, trade_date=args.trade_date)
            payload = {"status": "success", "paths": paths}
        else:
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        payload = {"status": "failed", "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None, sort_keys=getattr(args, "pretty", False)))
    return 0


def _artifact_paths(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "monitoring_report_path": args.monitoring_report_path,
        "production_orchestrator_report_path": args.production_orchestrator_report_path,
        "risk_control_report_path": args.risk_control_report_path,
        "eod_reconciliation_report_path": args.eod_reconciliation_report_path,
        "freeze_validation_report_path": args.freeze_validation_report_path,
        "portfolio_certification_decision_path": args.portfolio_certification_decision_path,
    }


def _required_incident_id(args: argparse.Namespace) -> str:
    if not args.incident_id:
        raise ValueError("incident-id is required")
    return args.incident_id


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for live readiness gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .decision import make_live_readiness_decision
from .models import LiveReadinessPolicy
from .policy import build_policy
from .report import write_live_readiness_artifacts
from .scorecard import build_live_readiness_scorecard


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build live readiness scorecards and decisions.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-policy", "scorecard", "decide", "run", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy-path")
    parser.add_argument("--policy-profile", default="sample_lenient_readiness")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--production-replay-report-path")
    parser.add_argument("--shadow-lab-report-path")
    parser.add_argument("--incident-report-path")
    parser.add_argument("--monitoring-report-path")
    parser.add_argument("--model-registry-report-path")
    parser.add_argument("--factor-certification-decision-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--risk-control-report-path")
    parser.add_argument("--settlement-report-path")
    parser.add_argument("--eod-reconciliation-report-path")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    policy = _load_policy(args.policy_path) if args.policy_path else build_policy(args.policy_profile)
    scorecard = build_live_readiness_scorecard(
        policy,
        production_replay_report_path=args.production_replay_report_path,
        shadow_lab_report_path=args.shadow_lab_report_path,
        incident_report_path=args.incident_report_path,
        monitoring_report_path=args.monitoring_report_path,
        model_registry_report_path=args.model_registry_report_path,
        factor_certification_decision_path=args.factor_certification_decision_path,
        portfolio_certification_decision_path=args.portfolio_certification_decision_path,
        freeze_validation_report_path=args.freeze_validation_report_path,
        risk_control_report_path=args.risk_control_report_path,
        settlement_report_path=args.settlement_report_path,
        eod_reconciliation_report_path=args.eod_reconciliation_report_path,
    )
    decision = make_live_readiness_decision(scorecard)
    paths = write_live_readiness_artifacts(policy, scorecard, decision, args.output_dir)
    payload = decision.to_dict()
    payload["scorecard"] = scorecard.to_dict()
    payload["paths"] = paths
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if decision.passed or args.command in {"scorecard", "init-policy", "report", "smoke"} else 1


def _load_policy(path: str) -> LiveReadinessPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = set(LiveReadinessPolicy.__dataclass_fields__)
    return LiveReadinessPolicy(**{key: payload[key] for key in allowed if key in payload})


if __name__ == "__main__":
    raise SystemExit(main())

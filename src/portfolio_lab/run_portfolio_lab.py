"""CLI for portfolio policy lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import PortfolioLabConfig
from .policy_grid import generate_portfolio_policy_grid, load_policy_grid, write_policy_grid
from .runner import run_portfolio_lab


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run portfolio policy lab trials.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-grid", "run", "resume", "aggregate", "select-policy", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_common(cmd)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backtest-root-dir")
    parser.add_argument("--universe-name")
    parser.add_argument("--factor-id")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="composite")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--latest-production-candidate", action="store_true")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--model-version-id")
    parser.add_argument("--factor-certification-decision-path")
    parser.add_argument("--validation-lab-report-path")
    parser.add_argument("--alpha-factory-report-path")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--scenario-profile", default="sample")
    parser.add_argument("--scenario-config-path")
    parser.add_argument("--policy-grid-path")
    parser.add_argument("--portfolio-methods", default="equal_weight,risk_aware")
    parser.add_argument("--risk-aversions", default="0.5,1.0")
    parser.add_argument("--turnover-penalties", default="0.0,0.1")
    parser.add_argument("--benchmark-weights", default="1.0")
    parser.add_argument("--max-weight-values", default="0.10")
    parser.add_argument("--max-names-values", default="2,20")
    parser.add_argument("--max-turnover-values", default="1.0")
    parser.add_argument("--max-tracking-error-values", default="1.0")
    parser.add_argument("--top-n-values", default="2,20")
    parser.add_argument("--max-trials", type=int)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--use-compute-scheduler", action="store_true")
    parser.add_argument("--compute-state-dir")
    parser.add_argument("--max-parallel-cpu-jobs", type=int, default=1)
    parser.add_argument("--max-parallel-gpu-jobs", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = PortfolioLabConfig(
        data_dir=args.data_dir,
        factor_store_dir=args.factor_store_dir,
        output_dir=args.output_dir,
        factor_id=args.factor_id,
        factor_type=args.factor_type,
        latest_approved=args.latest_approved or not bool(args.factor_id),
        index_code=args.index_code,
        scenario_profile=args.scenario_profile,
        max_trials=args.max_trials,
        pretty=args.pretty,
        metadata={
            "index_code": args.index_code,
            "data_freeze_dir": args.data_freeze_dir,
            "data_version_manifest_path": args.data_version_manifest_path,
            "require_data_freeze": bool(args.require_data_freeze),
            "factor_certification_decision_path": args.factor_certification_decision_path,
            "validation_lab_report_path": args.validation_lab_report_path,
            "alpha_factory_report_path": args.alpha_factory_report_path,
            "feature_set_manifest_path": args.feature_set_manifest_path,
            "capacity_aware": bool(args.capacity_aware),
            "settlement_aware": bool(args.settlement_aware),
            "corporate_action_aware": bool(args.corporate_action_aware),
            "point_in_time": bool(args.point_in_time),
            "risk_controls": bool(args.risk_controls),
            "use_compute_scheduler": bool(args.use_compute_scheduler),
            "compute_state_dir": args.compute_state_dir,
            "backtest_root_dir": args.backtest_root_dir,
            "universe_name": args.universe_name,
            "as_of_date": args.as_of_date,
        },
    )
    try:
        if args.command == "init-grid":
            policies = _policies_from_args(args)
            path = write_policy_grid(policies, args.output_dir)
            payload = {"policy_grid_path": str(path), "policy_count": len(policies)}
        else:
            policies = load_policy_grid(args.policy_grid_path) if args.policy_grid_path else _policies_from_args(args)
            result = run_portfolio_lab(config, policies=policies)
            payload = {"status": result.status, **result.summary, "paths": result.paths}
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def _policies_from_args(args) -> list:
    return generate_portfolio_policy_grid(
        factor_id=args.factor_id,
        methods=_strs(args.portfolio_methods),
        risk_aversions=_floats(args.risk_aversions),
        turnover_penalties=_floats(args.turnover_penalties),
        benchmark_weights=_floats(args.benchmark_weights),
        max_weight_values=_floats(args.max_weight_values),
        max_names_values=_ints(args.max_names_values),
        max_turnover_values=_floats(args.max_turnover_values),
        max_tracking_error_values=_floats(args.max_tracking_error_values),
        top_n_values=_ints(args.top_n_values),
        index_code=args.index_code,
        use_factor_risk_model=args.use_factor_risk_model,
        max_trials=args.max_trials,
    )


def _strs(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _floats(value: str) -> list[float]:
    return [float(item) for item in _strs(value)]


def _ints(value: str) -> list[int]:
    return [int(float(item)) for item in _strs(value)]


if __name__ == "__main__":
    raise SystemExit(main())

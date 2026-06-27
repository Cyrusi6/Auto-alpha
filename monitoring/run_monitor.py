"""CLI for local production monitoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_store import LocalFactorStore

from .checks import (
    check_active_risk_drift,
    check_attribution_anomaly,
    check_broker_file_outbox,
    check_broker_idempotency,
    check_broker_reconciliation,
    check_broker_rejected_orders,
    check_open_broker_orders,
    check_capacity_warnings,
    check_data_freshness,
    check_execution_quality,
    check_factor_risk_concentration,
    check_factor_drift,
    check_impact_cost_spike,
    check_order_fill_quality,
    check_paper_account,
    check_quality_report,
    check_risk_report,
    check_style_exposure_drift,
    check_unfilled_orders,
)
from .report import build_monitoring_report, write_monitoring_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate local production monitoring artifacts.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--paper-account-dir", required=True)
    parser.add_argument("--orders-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--risk-report-path")
    parser.add_argument("--risk-exposures-path")
    parser.add_argument("--risk-decomposition-path")
    parser.add_argument("--return-attribution-path")
    parser.add_argument("--capacity-report-path")
    parser.add_argument("--execution-quality-path")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-batch-id")
    parser.add_argument("--broker-reconciliation-path")
    parser.add_argument("--broker-outbox-manifest-path")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    checks = {}
    alerts = []
    for name, func in [
        ("data_freshness", lambda: check_data_freshness(args.data_dir, args.as_of_date)),
        ("quality_report", lambda: check_quality_report(args.data_dir)),
        ("factor_drift", lambda: check_factor_drift(LocalFactorStore(args.factor_store_dir), args.factor_id)),
        ("risk_report", lambda: check_risk_report(args.risk_report_path or _default_risk_path(args.orders_dir))),
        ("style_exposure_drift", lambda: check_style_exposure_drift(args.risk_exposures_path or _default_path(args.orders_dir, "risk_exposures.jsonl"))),
        ("active_risk_drift", lambda: check_active_risk_drift(args.risk_decomposition_path or _default_path(args.orders_dir, "risk_decomposition.jsonl"))),
        ("factor_risk_concentration", lambda: check_factor_risk_concentration(args.risk_report_path or _default_risk_path(args.orders_dir))),
        ("attribution_anomaly", lambda: check_attribution_anomaly(args.return_attribution_path or _default_path(args.orders_dir, "return_attribution.jsonl"))),
        ("capacity_warnings", lambda: check_capacity_warnings(args.capacity_report_path or _default_plan_path(args.orders_dir, "capacity_report.json"))),
        ("execution_quality", lambda: check_execution_quality(args.execution_quality_path or _default_plan_path(args.orders_dir, "execution_quality.json"))),
        ("unfilled_orders", lambda: check_unfilled_orders(args.execution_quality_path or _default_plan_path(args.orders_dir, "execution_quality.json"))),
        ("impact_cost_spike", lambda: check_impact_cost_spike(args.capacity_report_path or _default_plan_path(args.orders_dir, "capacity_report.json"))),
        (
            "broker_reconciliation",
            lambda: check_broker_reconciliation(args.broker_reconciliation_path or _default_broker_path(args.orders_dir, "broker_reconciliation.json")),
        ),
        ("open_broker_orders", lambda: check_open_broker_orders(args.broker_store_dir or _default_broker_store(args.orders_dir), args.broker_batch_id)),
        ("broker_rejected_orders", lambda: check_broker_rejected_orders(args.broker_store_dir or _default_broker_store(args.orders_dir), args.broker_batch_id)),
        ("broker_idempotency", lambda: check_broker_idempotency(args.broker_store_dir or _default_broker_store(args.orders_dir), args.broker_batch_id)),
        (
            "broker_file_outbox",
            lambda: check_broker_file_outbox(args.broker_outbox_manifest_path or _default_broker_outbox_manifest(args.orders_dir)),
        ),
        ("fill_quality", lambda: check_order_fill_quality(Path(args.orders_dir) / "paper_fills.jsonl")),
        ("paper_account", lambda: check_paper_account(args.paper_account_dir)),
    ]:
        payload, check_alerts = func()
        checks[name] = payload
        alerts.extend(check_alerts)
    report = build_monitoring_report(args.as_of_date, checks, alerts)
    json_path, md_path, alerts_path = write_monitoring_report(report, args.output_dir)
    payload = report.to_dict() | {
        "paths": {
            "monitoring_report_path": str(json_path),
            "monitoring_report_md_path": str(md_path),
            "alerts_path": str(alerts_path),
        }
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if not any(alert.severity == "error" for alert in alerts) else 1


def _default_risk_path(orders_dir: str | Path) -> str:
    root = Path(orders_dir)
    for name in ("risk_model_report.json", "risk_report.json"):
        path = root / name
        if path.exists():
            return str(path)
    return ""


def _default_path(orders_dir: str | Path, filename: str) -> str:
    path = Path(orders_dir) / filename
    return str(path) if path.exists() else ""


def _default_plan_path(orders_dir: str | Path, filename: str) -> str:
    root = Path(orders_dir)
    for path in (root / filename, root / "plan" / filename):
        if path.exists():
            return str(path)
    return ""


def _default_broker_path(orders_dir: str | Path, filename: str) -> str:
    root = Path(orders_dir)
    for path in (root / filename, root / "broker" / filename, root.parent / "production_execute" / "broker" / filename):
        if path.exists():
            return str(path)
    return ""


def _default_broker_store(orders_dir: str | Path) -> str:
    root = Path(orders_dir)
    for path in (root / "broker", root.parent / "broker"):
        if (path / "broker_order_state.json").exists():
            return str(path)
    return ""


def _default_broker_outbox_manifest(orders_dir: str | Path) -> str:
    root = Path(orders_dir)
    for path in (
        root / "broker_instruction_manifest.json",
        root / "outbox" / "broker_instruction_manifest.json",
        root.parent / "broker_file" / "outbox" / "broker_instruction_manifest.json",
    ):
        if path.exists():
            return str(path)
    return ""


if __name__ == "__main__":
    raise SystemExit(main())

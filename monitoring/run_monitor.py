"""CLI for local production monitoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_store import LocalFactorStore

from .checks import (
    check_data_freshness,
    check_factor_drift,
    check_order_fill_quality,
    check_paper_account,
    check_quality_report,
    check_risk_report,
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
    path = Path(orders_dir) / "risk_report.json"
    return str(path) if path.exists() else ""


if __name__ == "__main__":
    raise SystemExit(main())

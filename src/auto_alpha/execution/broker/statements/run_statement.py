"""CLI for local broker statement import and synthesis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .importer import import_statement, read_normalized_statement
from .synthesizer import synthesize_statement_from_internal
from .validator import validate_statement_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import or synthesize generic broker statement files.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("import", "validate", "synthesize-from-internal", "show-summary"):
        command = sub.add_parser(name)
        command.add_argument("--source-dir")
        command.add_argument("--output-dir")
        command.add_argument("--schema-name", default="generic_broker_statement", choices=["generic_broker_statement", "qmt_statement_skeleton"])
        command.add_argument("--schema-config")
        command.add_argument("--account-id")
        command.add_argument("--broker-name")
        command.add_argument("--trade-date")
        command.add_argument("--as-of-date")
        command.add_argument("--broker-store-dir")
        command.add_argument("--broker-batch-id")
        command.add_argument("--paper-account-dir")
        command.add_argument("--settlement-dir")
        command.add_argument("--inject-cash-diff", type=float, default=0.0)
        command.add_argument("--inject-position-diff", action="append", default=[])
        command.add_argument("--drop-fill", action="append", default=[])
        command.add_argument("--duplicate-fill", action="append", default=[])
        command.add_argument("--inject-fee-diff", action="append", default=[])
        command.add_argument("--strict", action="store_true")
        command.add_argument("--fail-on-error", action="store_true")
        command.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "synthesize-from-internal":
        if not args.output_dir:
            raise ValueError("--output-dir is required")
        payload = synthesize_statement_from_internal(
            output_dir=args.output_dir,
            broker_store_dir=args.broker_store_dir,
            broker_batch_id=args.broker_batch_id,
            paper_account_dir=args.paper_account_dir,
            settlement_dir=args.settlement_dir,
            account_id=args.account_id or "paper_ashare",
            broker_name=args.broker_name or "synthetic_broker",
            trade_date=args.trade_date or "",
            as_of_date=args.as_of_date or args.trade_date or "",
            inject_cash_diff=args.inject_cash_diff,
            inject_position_diff=args.inject_position_diff,
            drop_fill=args.drop_fill,
            duplicate_fill=args.duplicate_fill,
            inject_fee_diff=args.inject_fee_diff,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command == "import":
        if not args.source_dir or not args.output_dir:
            raise ValueError("--source-dir and --output-dir are required")
        result = import_statement(
            source_dir=args.source_dir,
            output_dir=args.output_dir,
            schema_config=args.schema_config,
            account_id=args.account_id,
            broker_name=args.broker_name,
            trade_date=args.trade_date,
            as_of_date=args.as_of_date,
            schema_name=args.schema_name,
            strict=args.strict,
        )
        payload = result.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1 if args.fail_on_error and result.validation.error_count else 0
    if args.command == "validate":
        source = args.source_dir or args.output_dir
        if not source:
            raise ValueError("--source-dir or --output-dir is required")
        report = validate_statement_dir(source, as_of_date=args.as_of_date or "", strict=args.strict)
        payload = report.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1 if args.fail_on_error and report.error_count else 0
    if args.command == "show-summary":
        source = Path(args.source_dir or args.output_dir or ".")
        payload = {
            "source_dir": str(source),
            "datasets": {dataset: len(rows) for dataset, rows in read_normalized_statement(source).items()},
        }
        manifest_path = source / "broker_statement_manifest.json"
        if manifest_path.exists():
            payload["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

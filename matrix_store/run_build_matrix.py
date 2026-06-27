"""CLI for building local A-share matrix caches."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace

from .builder import build_matrix_cache
from .validator import validate_matrix_cache


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a local matrix cache from A-share JSONL artifacts.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--fields")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    fields = [item.strip() for item in args.fields.split(",") if item.strip()] if args.fields else None
    result = build_matrix_cache(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        fields=fields,
    )
    if args.validate:
        report = validate_matrix_cache(result.cache_dir)
        result = replace(result, validation_report_path=f"{result.cache_dir}/matrix_validation_report.json")
        payload = result.to_dict() | {"validation": report.to_dict()}
    else:
        payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

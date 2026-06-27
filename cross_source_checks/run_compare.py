"""CLI for comparing two local governed A-share data directories."""

from __future__ import annotations

import argparse
import json

from .comparator import compare_data_dirs
from .report import write_cross_source_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two local A-share data directories.")
    parser.add_argument("--left-data-dir", required=True)
    parser.add_argument("--right-data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--datasets", required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    datasets = [item.strip() for item in args.datasets.split(",") if item.strip()]
    report = compare_data_dirs(args.left_data_dir, args.right_data_dir, datasets)
    json_path, md_path = write_cross_source_report(report, args.output_dir)
    payload = report.to_dict() | {
        "cross_source_report_path": str(json_path),
        "cross_source_report_md_path": str(md_path),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

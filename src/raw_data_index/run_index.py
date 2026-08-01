"""CLI for raw JSONL sidecar indexes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_pipeline.ashare import AShareDataConfig, AShareDataManager

from .models import RawDataIndexStatus
from .registry import default_datasets
from .report import dumps, write_raw_data_index_artifacts
from .scanner import active_run_safety_check, build_raw_data_index
from .validator import validate_raw_data_index


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate sidecar indexes for A-share raw JSONL datasets.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "build", "validate", "report", "smoke"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir")
        cmd.add_argument("--run-dir")
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--datasets")
        cmd.add_argument("--start-date")
        cmd.add_argument("--end-date")
        cmd.add_argument("--profile-name")
        cmd.add_argument("--partition-granularity", choices=["monthly", "daily", "none"], default="monthly")
        cmd.add_argument("--max-records", type=int)
        cmd.add_argument("--read-only", action="store_true")
        cmd.add_argument("--plan-only", action="store_true")
        cmd.add_argument("--allow-active-run-index", action="store_true")
        cmd.add_argument("--write-sidecar-to-data-dir", action="store_true")
        cmd.add_argument("--fail-on-stale", action="store_true")
        cmd.add_argument("--hash-check", action="store_true")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir) if args.data_dir else output_dir / "sample_data"
    selected = _split(args.datasets) or (["securities", "trade_calendar", "daily_bars", "daily_basic"] if args.command == "smoke" else default_datasets())
    if args.command == "smoke":
        AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True, mode="overwrite")
    if args.command == "plan" or args.plan_only:
        safety = active_run_safety_check(
            data_dir=data_dir,
            run_dir=args.run_dir,
            selected_datasets=selected,
            allow_active_run_index=args.allow_active_run_index,
        )
        payload = {
            "status": RawDataIndexStatus.blocked if safety.get("blocked") else RawDataIndexStatus.planned,
            "data_dir": str(data_dir),
            "datasets": selected,
            "dataset_count": len(selected),
            "partition_granularity": args.partition_granularity,
            "safety": safety,
            "commands": {
                "build": _build_command(args, data_dir, selected),
                "validate": f"uv run python -m raw_data_index.run_index validate --data-dir {data_dir} --output-dir {output_dir} --pretty",
            },
        }
        paths = write_raw_data_index_artifacts(
            manifest=None,
            dataset_indexes=[],
            partitions=[],
            validation=None,
            issues=safety.get("issues", []),
            output_dir=output_dir,
            data_dir=data_dir,
            status=payload["status"],
        )
        payload["paths"] = paths
        print(dumps(payload, args.pretty))
        return 0
    if args.command in {"build", "smoke", "report"}:
        target_dir = data_dir / "raw_data_index" if args.write_sidecar_to_data_dir else output_dir
        manifest, indexes, partitions, issues, safety = build_raw_data_index(
            data_dir=data_dir,
            datasets=selected,
            output_dir=target_dir,
            profile_name=args.profile_name,
            start_date=args.start_date,
            end_date=args.end_date,
            partition_granularity=args.partition_granularity,
            max_records=args.max_records,
            run_dir=args.run_dir,
            allow_active_run_index=args.allow_active_run_index,
        )
        if manifest is None:
            paths = write_raw_data_index_artifacts(
                manifest=None,
                dataset_indexes=[],
                partitions=[],
                validation=None,
                issues=issues,
                output_dir=target_dir,
                data_dir=data_dir,
                status=RawDataIndexStatus.blocked,
            )
            payload = {"status": RawDataIndexStatus.blocked, "safety": safety, "paths": paths}
            print(dumps(payload, args.pretty))
            return 1
        # Validate against the manifest object after it is written below.
        paths = write_raw_data_index_artifacts(
            manifest=manifest,
            dataset_indexes=indexes,
            partitions=partitions,
            validation=None,
            issues=issues,
            output_dir=target_dir,
            data_dir=data_dir,
            status=manifest.status,
        )
        validation = validate_raw_data_index(paths["raw_data_index_manifest_path"], data_dir=data_dir, hash_check=args.hash_check)
        paths = write_raw_data_index_artifacts(
            manifest=manifest,
            dataset_indexes=indexes,
            partitions=partitions,
            validation=validation,
            issues=[*issues, *validation.issues],
            output_dir=target_dir,
            data_dir=data_dir,
            status=validation.status,
        )
        payload = {
            "status": validation.status,
            "index_id": manifest.index_id,
            "dataset_count": manifest.dataset_count,
            "total_records": manifest.total_records,
            "partition_count": manifest.partition_count,
            "index_hash": manifest.index_hash,
            "paths": paths,
        }
        print(dumps(payload, args.pretty))
        return 1 if args.fail_on_stale and validation.status in {RawDataIndexStatus.stale, RawDataIndexStatus.failed} else 0
    if args.command == "validate":
        manifest_path = output_dir / "raw_data_index_manifest.json"
        validation = validate_raw_data_index(manifest_path, data_dir=data_dir if args.data_dir else None, hash_check=args.hash_check)
        paths = write_raw_data_index_artifacts(
            manifest=None,
            dataset_indexes=[],
            partitions=[],
            validation=validation,
            issues=validation.issues,
            output_dir=output_dir,
            data_dir=data_dir,
            status=validation.status,
            write_tables=False,
        )
        payload = validation.to_dict() | {"paths": paths}
        print(dumps(payload, args.pretty))
        return 1 if args.fail_on_stale and validation.status in {RawDataIndexStatus.stale, RawDataIndexStatus.failed} else 0
    return 0


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _build_command(args: argparse.Namespace, data_dir: Path, datasets: list[str]) -> str:
    parts = [
        "uv run python -m raw_data_index.run_index build",
        f"--data-dir {data_dir}",
        f"--output-dir {args.output_dir}",
        f"--datasets {','.join(datasets)}",
        f"--partition-granularity {args.partition_granularity}",
    ]
    if args.run_dir:
        parts.append(f"--run-dir {args.run_dir}")
    if args.allow_active_run_index:
        parts.append("--allow-active-run-index")
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())

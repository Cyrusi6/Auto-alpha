"""CLI for feature set manifest and tensor generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_lake import validate_research_input
from model_core.data_loader import AShareDataLoader

from .builder import build_feature_tensor_artifacts, load_feature_manifest
from .catalog import build_feature_set_manifest
from .report import write_feature_factory_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate versioned A-share feature sets.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "validate", "report"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir")
        cmd.add_argument("--data-freeze-dir")
        cmd.add_argument("--data-version-manifest-path")
        cmd.add_argument("--matrix-cache-dir")
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
        cmd.add_argument("--feature-set-name", default="ashare_features_v1")
        cmd.add_argument("--feature-set-version", default="1.0")
        cmd.add_argument("--feature-set-manifest-path")
        cmd.add_argument("--feature-promotion-policy-path")
        cmd.add_argument("--feature-promotion-allowlist-path")
        cmd.add_argument("--apply-feature-promotion", action="store_true")
        cmd.add_argument("--raw-data-index-manifest-path")
        cmd.add_argument("--require-raw-data-index", action="store_true")
        cmd.add_argument("--point-in-time", action="store_true")
        cmd.add_argument("--corporate-action-aware", action="store_true")
        cmd.add_argument(
            "--target-return-mode",
            choices=["adjusted_close", "raw_close", "corporate_action_total_return"],
            default="adjusted_close",
        )
        cmd.add_argument("--require-data-freeze", action="store_true")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.command in {"validate", "report"} and args.feature_set_manifest_path:
        manifest = load_feature_manifest(args.feature_set_manifest_path)
        payload = manifest.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0

    freeze_report = validate_research_input(
        data_dir=args.data_dir,
        data_freeze_dir=args.data_freeze_dir,
        require_freeze=args.require_data_freeze,
    )
    if freeze_report.error_count:
        raise RuntimeError(f"data freeze validation failed: {freeze_report.status}")
    data_dir = _resolve_data_dir(args.data_dir, args.data_freeze_dir)
    if args.command == "validate":
        manifest = build_feature_set_manifest(
            args.feature_set_name,
            args.feature_set_version,
            data_freeze_id=freeze_report.freeze_id,
            data_freeze_hash=freeze_report.content_hash,
            point_in_time=args.point_in_time,
            corporate_action_aware=args.corporate_action_aware,
            target_return_mode=args.target_return_mode,
        )
        payload = manifest.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0

    loader = AShareDataLoader(
        data_dir=data_dir,
        device=None if args.device == "auto" else args.device,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=bool(args.matrix_cache_dir and (Path(args.matrix_cache_dir) / "metadata.json").exists()),
        point_in_time=args.point_in_time,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
    ).load_data()
    result = build_feature_tensor_artifacts(
        loader,
        output_dir,
        feature_set_name=args.feature_set_name,
        feature_set_version=args.feature_set_version,
        data_freeze_id=freeze_report.freeze_id,
        data_freeze_hash=freeze_report.content_hash,
        point_in_time=args.point_in_time,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        feature_promotion_policy_path=args.feature_promotion_policy_path,
        feature_promotion_allowlist_path=args.feature_promotion_allowlist_path,
        apply_feature_promotion=args.apply_feature_promotion,
        raw_data_index_manifest_path=args.raw_data_index_manifest_path,
        require_raw_data_index=args.require_raw_data_index,
    )
    write_feature_factory_report(result.to_dict(), output_dir)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _resolve_data_dir(data_dir: str | None, data_freeze_dir: str | None) -> str | None:
    if not data_freeze_dir:
        return data_dir
    freeze_root = Path(data_freeze_dir)
    physical_data_dir = freeze_root / "data"
    if physical_data_dir.exists():
        return str(physical_data_dir)
    manifest_path = freeze_root / "freeze_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_data_dir = manifest.get("source_data_dir")
        if source_data_dir and Path(source_data_dir).exists():
            return str(Path(source_data_dir))
    return data_dir


if __name__ == "__main__":
    raise SystemExit(main())

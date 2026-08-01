"""CLI for local model registry operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_store import LocalFactorStore

from .lineage import build_model_lineage_graph
from .models import ModelKind, ModelLifecycleAction, ModelLifecycleStatus
from .report import write_lineage_graph, write_model_registry_report
from .store import LocalModelRegistry


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local model registry records.")
    parser.add_argument("--registry-dir", required=True)
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in [
        "register-factor",
        "register-production-candidate-bundle",
        "show-production-candidates",
        "list-models",
        "show-model",
        "show-active",
        "activate",
        "pause",
        "resume",
        "quarantine",
        "retire",
        "rollback",
        "lineage",
        "report",
    ]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--factor-id")
        cmd.add_argument("--model-version-id")
        cmd.add_argument("--model-kind", default=ModelKind.composite_factor)
        cmd.add_argument("--approval-id")
        cmd.add_argument("--actor", default="local_operator")
        cmd.add_argument("--reason")
        cmd.add_argument("--environment", default="paper")
        cmd.add_argument("--artifact-dir", action="append", default=[])
        cmd.add_argument("--artifact-catalog-path", action="append", default=[])
        cmd.add_argument("--production-candidate-bundle-path")
        cmd.add_argument("--dry-run", action="store_true")
        cmd.add_argument("--explicit-override", action="store_true")
        cmd.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry = LocalModelRegistry(args.registry_dir)
    try:
        payload = _run(args, registry)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _run(args: argparse.Namespace, registry: LocalModelRegistry) -> dict:
    if args.command == "register-factor":
        if not args.factor_store_dir or not args.factor_id:
            raise ValueError("register-factor requires --factor-store-dir and --factor-id")
        factors = LocalFactorStore(args.factor_store_dir).load_factors()
        factor = next((record for record in factors if record.factor_id == args.factor_id), None)
        if factor is None:
            raise FileNotFoundError(f"factor not found: {args.factor_id}")
        record = registry.register_factor_record(factor, model_kind=args.model_kind)
        write_model_registry_report(registry)
        return {"model_version": record.to_dict(), "model_version_id": record.model_version_id}
    if args.command in {"register-production-candidate-bundle", "show-production-candidates"}:
        if not args.production_candidate_bundle_path:
            raise ValueError(f"{args.command} requires --production-candidate-bundle-path")
        candidates = _read_jsonl(Path(args.production_candidate_bundle_path))
        payload = {
            "status": "dry_run" if args.dry_run or args.command == "show-production-candidates" else "recorded",
            "candidate_count": len(candidates),
            "candidates": candidates,
            "note": "production candidate bundles are not activated by this command",
        }
        if args.command == "register-production-candidate-bundle" and not args.dry_run:
            path = Path(args.registry_dir) / "production_candidate_bundle_registry.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            payload["production_candidate_bundle_registry_path"] = str(path)
        return payload
    if args.command == "list-models":
        return {"models": [record.to_dict() for record in registry.load_model_versions()]}
    if args.command == "show-model":
        record = registry.get_model_version(args.model_version_id)
        if record is None:
            raise FileNotFoundError(f"model version not found: {args.model_version_id}")
        return record.to_dict()
    if args.command == "show-active":
        record = registry.latest_active(model_kind=args.model_kind, environment=args.environment)
        deployment = registry.latest_active_deployment(model_kind=args.model_kind, environment=args.environment)
        return {"model_version": record.to_dict() if record else None, "deployment": deployment.to_dict() if deployment else None}
    if args.command == "activate":
        record, deployment = registry.activate(
            args.model_version_id,
            approval_id=args.approval_id,
            actor=args.actor,
            reason=args.reason,
            environment=args.environment,
            explicit_override=args.explicit_override,
        )
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return {"model_version": record.to_dict(), "deployment": deployment.to_dict()}
    if args.command == "pause":
        record = registry.pause(args.model_version_id, args.reason, args.actor)
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "resume":
        record = registry.transition(
            args.model_version_id,
            ModelLifecycleAction.resume,
            ModelLifecycleStatus.active,
            args.actor,
            args.reason,
            explicit_override=args.explicit_override,
        )
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "quarantine":
        record = registry.quarantine(args.model_version_id, args.reason, args.actor)
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "retire":
        record = registry.retire(args.model_version_id, args.reason, args.actor)
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "rollback":
        record, deployment = registry.rollback(
            model_kind=args.model_kind,
            environment=args.environment,
            actor=args.actor,
            reason=args.reason,
            explicit_override=args.explicit_override,
        )
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return {"model_version": record.to_dict(), "deployment": deployment.to_dict()}
    if args.command == "lineage":
        factor_store = LocalFactorStore(args.factor_store_dir) if args.factor_store_dir else None
        graph = build_model_lineage_graph(registry, factor_store, args.artifact_catalog_path, args.artifact_dir)
        path = write_lineage_graph(registry, graph)
        return graph.to_dict() | {"path": str(path)}
    if args.command == "report":
        json_path, md_path = write_model_registry_report(registry)
        return {"model_registry_report_path": str(json_path), "model_registry_report_md_path": str(md_path)}
    raise ValueError(f"unsupported command: {args.command}")


def _sync_if_possible(args: argparse.Namespace, registry: LocalModelRegistry, model_version_id: str) -> None:
    if args.factor_store_dir:
        registry.sync_factor_store_status(LocalFactorStore(args.factor_store_dir), model_version_id)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())

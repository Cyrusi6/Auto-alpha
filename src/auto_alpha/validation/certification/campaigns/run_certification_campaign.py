"""CLI for factor certification campaigns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .consolidate import consolidate_factor_certification_campaign
from .ingest import ingest_certification_queue
from .leaderboard import build_certified_factor_leaderboard
from .registry import LocalFactorCertificationCampaignStore
from .report import write_factor_certification_campaign_report
from .scheduler import run_factor_certification_campaign


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run factor certification campaign store workflows.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["ingest", "plan", "run", "consolidate", "leaderboard", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--certification-campaign-store-dir", required=True)
    parser.add_argument("--certification-campaign-id")
    parser.add_argument("--factor-certification-queue-path")
    parser.add_argument("--output-dir")
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--rank-range")
    parser.add_argument("--family-filter")
    parser.add_argument("--source-filter")
    parser.add_argument("--certification-policy-profile", default="sample_lenient_certification")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-validation-ready", action="store_true")
    parser.add_argument("--research-readiness-decision-path")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if payload.get("status") in {"success", "planned", "partial", "blocked", "ready"} else 1


def _run(args: argparse.Namespace) -> dict:
    store = LocalFactorCertificationCampaignStore(args.certification_campaign_store_dir)
    readiness = _readiness(args.research_readiness_decision_path)
    if args.require_validation_ready and not readiness["ready"]:
        json_path, md_path = write_factor_certification_campaign_report(store, {"blocked_reason": "validation readiness not satisfied", "readiness": readiness})
        return {"status": "blocked", "blocked_reason": "validation readiness not satisfied", "paths": store.paths() | {"factor_certification_campaign_report_path": str(json_path), "factor_certification_campaign_report_md_path": str(md_path)}}
    if args.command == "smoke":
        payload = _smoke(args)
    elif args.command == "ingest":
        if not args.factor_certification_queue_path:
            raise ValueError("--factor-certification-queue-path is required")
        payload = ingest_certification_queue(
            args.certification_campaign_store_dir,
            args.factor_certification_queue_path,
            certification_campaign_id=args.certification_campaign_id,
            max_items=args.max_items or None,
            rank_range=args.rank_range,
            family_filter=args.family_filter,
            source_filter=args.source_filter,
            policy_profile=args.certification_policy_profile,
        )
    elif args.command == "plan":
        payload = run_factor_certification_campaign(args.certification_campaign_store_dir, output_dir=args.output_dir, max_items=args.max_items or None, dry_run=True)
    elif args.command == "run":
        if args.factor_certification_queue_path and not store.load_items():
            ingest_certification_queue(
                args.certification_campaign_store_dir,
                args.factor_certification_queue_path,
                certification_campaign_id=args.certification_campaign_id,
                max_items=args.max_items or None,
                rank_range=args.rank_range,
                family_filter=args.family_filter,
                source_filter=args.source_filter,
                policy_profile=args.certification_policy_profile,
            )
        payload = run_factor_certification_campaign(
            args.certification_campaign_store_dir,
            output_dir=args.output_dir,
            max_items=args.max_items or None,
            resume=args.resume,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            payload = payload | consolidate_factor_certification_campaign(args.certification_campaign_store_dir)
            payload["leaderboard_count"] = len(build_certified_factor_leaderboard(args.certification_campaign_store_dir))
    elif args.command == "consolidate":
        payload = consolidate_factor_certification_campaign(args.certification_campaign_store_dir)
    elif args.command == "leaderboard":
        rows = build_certified_factor_leaderboard(args.certification_campaign_store_dir)
        payload = {"status": "success", "leaderboard_count": len(rows), "paths": store.paths()}
    else:
        raise ValueError(f"unsupported command: {args.command}")
    json_path, md_path = write_factor_certification_campaign_report(store, {"last_command": args.command})
    payload.setdefault("paths", store.paths())
    payload["paths"] = payload["paths"] | {"factor_certification_campaign_report_path": str(json_path), "factor_certification_campaign_report_md_path": str(md_path)}
    return payload


def _smoke(args: argparse.Namespace) -> dict:
    root = Path(args.output_dir or args.certification_campaign_store_dir)
    root.mkdir(parents=True, exist_ok=True)
    queue = root / "factor_certification_queue.jsonl"
    rows = [
        {
            "queue_id": "certq_smoke_0001",
            "validation_candidate_id": "vc_smoke_0001",
            "factor_id": "factor_smoke_0001",
            "priority": 1,
            "certification_policy_profile": args.certification_policy_profile,
            "validation_result_path": "",
            "factor_store_dir": str(root / "factor_store"),
            "status": "queued",
            "metadata": {"leaderboard": {"formula_hash": "hash_smoke_0001", "validation_score": 1.0}},
        }
    ]
    queue.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    return ingest_certification_queue(args.certification_campaign_store_dir, queue, certification_campaign_id=args.certification_campaign_id or "factor_certification_campaign_smoke", max_items=1)


def _readiness(path: str | None) -> dict:
    if not path:
        return {"ready": True, "status": "not_required"}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = bool(payload.get("validation_ready") or payload.get("can_run_validation") or payload.get("can_run_factor_certification"))
    ready = ready or str(payload.get("status")) in {"ready", "validation_ready", "ready_for_validation", "pass"}
    return {"ready": ready, "status": payload.get("status"), "path": str(target)}


if __name__ == "__main__":
    raise SystemExit(main())

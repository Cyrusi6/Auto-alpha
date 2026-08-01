"""CLI for Task 052-A governed preflight and conditional execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .audit import Task052AuditInputs, audit_task_052_inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Task 052-A governed audit.")
    parser.add_argument("--source-campaign-root", required=True)
    parser.add_argument("--task-051-output-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--observed-end-date", default="20260630")
    parser.add_argument("--backfill-root")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audit = audit_task_052_inputs(
        Task052AuditInputs(
            source_campaign_root=args.source_campaign_root,
            task_051_output_dir=args.task_051_output_dir,
            index_code=args.index_code,
            observed_end_date=args.observed_end_date,
        )
    )
    audit_path = write_json_artifact(
        output_dir / "task_052_preflight_audit.json",
        audit,
        "task_052_preflight_audit",
        "task_052_a",
    )
    backfill = _backfill_summary(Path(args.backfill_root)) if args.backfill_root else {}
    suspension_covered = int((backfill.get("suspensions") or {}).get("covered_stock_count", 0))
    blockers = []
    if suspension_covered != int(audit.get("historical_universe", {}).get("union_count", 0)):
        blockers.append("governed_suspension_backfill_not_completed")
    evidence_path = Path(args.backfill_root) / "task_052_backfill_evidence.json" if args.backfill_root else None
    if evidence_path and evidence_path.exists():
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        unknown_timing = int((((evidence.get("datasets") or {}).get("suspensions") or {}).get("suspend_timing_unknown_count", 0)) or 0)
        if unknown_timing:
            blockers.append(f"suspension_timing_semantics_unproven:{unknown_timing}")
    blockers.extend([
        "strict_matrix_real_generation_not_published",
        "v3_values_validity_real_generation_not_published",
        "real_firewall_sentinel_not_executed_on_new_matrix",
    ])
    readiness = {
        "status": "blocked" if blockers else "ready_for_retrospective_replay",
        "data_foundation_ready": False,
        "retrospective_replay_ready": False,
        "research_firewall_ready": False,
        "feature_family_ready": {},
        "untouched_holdout_ready": False,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "publication_timestamp_proven": False,
        "conservative_availability_lag_trade_days": 1,
        "evidence_level": "retrospective_pit_proxy",
        "candidate_count": int(audit.get("candidate_pool", {}).get("candidate_count", 0)),
        "gpu_replay_started": False,
        "backfill_coverage": backfill,
        "selection_data_reused": True,
        "untouched_holdout": False,
        "certification_blockers": ["constituent_publication_timing_unknown", "no_future_untouched_holdout", "selection_data_reused"],
        "certification_queue_count": 0,
        "portfolio_queue_count": 0,
        "paper_queue_count": 0,
        "live_queue_count": 0,
        "blockers": blockers,
    }
    readiness_path = write_json_artifact(
        output_dir / "task_052_readiness.json",
        readiness,
        "task_052_readiness",
        "task_052_a",
    )
    payload = {
        "status": readiness["status"],
        "paths": {"preflight_audit": str(audit_path), "readiness": str(readiness_path)},
        "readiness": readiness,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def _backfill_summary(root: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for dataset in ("suspensions", "st_status_daily", "name_changes"):
        ledger = root / "staging" / dataset / "coverage_ledger.jsonl"
        successful: set[str] = set()
        failed: set[str] = set()
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                code = str(row.get("ts_code") or "")
                if row.get("status") == "success":
                    successful.add(code)
                    failed.discard(code)
                elif code not in successful:
                    failed.add(code)
        result[dataset] = {"covered_stock_count": len(successful), "blocked_stock_count": len(failed)}
    return result


if __name__ == "__main__":
    raise SystemExit(main())

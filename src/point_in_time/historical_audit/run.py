"""Real-data preflight and proof-gated Task 051-A workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from universe.historical import SnapshotPolicy, build_historical_index_universe, normalize_suspensions
from validation_campaign_store.artifacts import resolve_campaign_artifacts


REQUIRED_DATASETS = (
    "index_members", "securities", "trade_calendar", "daily_bars", "daily_limits", "daily_basic",
    "suspensions", "name_changes", "adjustment_factors", "corporate_actions",
)


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="Run Task 051-A historical PIT proof audit")
    parser.add_argument("--source-campaign-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--expected-member-count", type=int, default=300)
    parser.add_argument("--max-staleness-calendar-days", type=int, default=45)
    parser.add_argument("--pretty", action="store_true")
    args=parser.parse_args(argv)
    payload=run_audit(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if payload["status"] in {"blocked", "success"} else 1


def run_audit(args) -> dict[str, Any]:
    output=Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    artifacts=resolve_campaign_artifacts(args.source_campaign_root)
    freeze=Path(artifacts.data_freeze_dir)
    version_path=freeze / "dataset_version_manifest.json"
    version=json.loads(version_path.read_text(encoding="utf-8"))
    raw_index_path=Path(version["raw_data_index_manifest_path"])
    raw_index=json.loads(raw_index_path.read_text(encoding="utf-8"))
    indexed={str(row["dataset"]):row for row in raw_index.get("datasets", [])}
    inputs=[]; blockers=[]
    for dataset in REQUIRED_DATASETS:
        row=dict(indexed.get(dataset) or {})
        path=Path(row.get("records_path") or Path(artifacts.data_dir) / dataset / "records.jsonl")
        ready=path.exists() and row.get("status") == "fresh" and int(row.get("parse_error_count",0) or 0)==0
        inputs.append({
            "dataset":dataset,"path":str(path),"sha256":row.get("records_sha256") or (_sha256(path) if path.exists() else None),
            "record_count":int(row.get("record_count",0) or 0),"first_date":row.get("first_date"),"last_date":row.get("last_date"),
            "primary_key_fields":row.get("primary_key_fields",[]),"duplicate_key_count_estimate":int(row.get("duplicate_key_count_estimate",0) or 0),
            "source":"governed_raw_data_index","readiness":"ready" if ready else "blocked",
        })
        if not ready: blockers.append(f"dataset_not_ready:{dataset}")
    supplemental=[
        ("freeze_manifest",freeze / "freeze_manifest.json"),("dataset_version_manifest",version_path),
        ("raw_data_index_manifest",raw_index_path),("matrix_cache",Path(artifacts.matrix_cache_dir)/"matrix_version_manifest.json"),
        ("feature_manifest",Path(artifacts.feature_manifest_path)),("feature_tensor",Path(artifacts.feature_tensor_path)),
        ("promotion_policy",Path(artifacts.promotion_policy_path)),("promotion_allowlist",Path(artifacts.promotion_allowlist_path)),
        ("promotion_denylist",Path(artifacts.promotion_denylist_path)),("candidate_pool",Path(artifacts.candidate_pool_path)),
    ]
    for name,path in supplemental:
        inputs.append({"dataset":name,"path":str(path),"sha256":_sha256(path) if path.exists() else None,"record_count":None,"first_date":None,"last_date":None,"primary_key_fields":[],"duplicate_key_count_estimate":0,"source":"campaign_manifest_or_catalog","readiness":"ready" if path.exists() else "blocked"})
        if not path.exists(): blockers.append(f"artifact_missing:{name}")
    suspension_path=Path(artifacts.data_dir)/"suspensions"/"records.jsonl"
    suspension_rows=_read_jsonl(suspension_path)
    normalized_suspensions,suspension_blockers=normalize_suspensions(suspension_rows)
    if not normalized_suspensions:
        blockers.append("suspension_source_has_no_dated_events")
    preflight={
        "status":"blocked" if blockers else "ready", "created_at":_utc_now(), "source_campaign_id":_campaign_id(Path(artifacts.campaign_manifest_path)),
        "inputs":inputs,"blockers":sorted(set(blockers)),"suspension_record_count":len(suspension_rows),
        "dated_suspension_event_count":len(normalized_suspensions),"unknown_suspension_event_count":len(suspension_blockers),
        "raw_data_index_hash":raw_index.get("index_hash"),"freeze_hash":json.loads((freeze/"freeze_manifest.json").read_text()).get("content_hash"),
    }
    preflight_path=write_json_artifact(output/"task_051_preflight_audit.json",preflight,"task_051_preflight_audit","task_051_a")
    (output/"task_051_preflight_audit.md").write_text(_preflight_md(preflight),encoding="utf-8")
    policy=SnapshotPolicy(index_code=args.index_code,expected_member_count=args.expected_member_count,max_staleness_calendar_days=args.max_staleness_calendar_days)
    historical=build_historical_index_universe(Path(artifacts.data_dir)/"index_members"/"records.jsonl",Path(artifacts.data_dir)/"trade_calendar"/"records.jsonl",output/"historical_universe",policy)
    proof=json.loads(Path(historical.proof_manifest_path).read_text())
    proof_blockers=list(proof.get("blockers") or [])
    proof_blockers.extend(preflight["blockers"])
    proof_blockers.extend(["st_status_known_incomplete","constituent_publication_timing_unknown","no_future_untouched_holdout"])
    observation=_observation_ledger(Path(args.source_campaign_root),Path(artifacts.matrix_cache_dir))
    ledger_path=write_jsonl_artifact(output/"research_observation_ledger.jsonl",observation,"research_observation_ledger","task_051_a")
    max_observed=max((str(row["max_observed_target_date"]) for row in observation if row.get("max_observed_target_date")),default=None)
    holdout={
        "status":"waiting_for_future_data","max_observed_target_date":max_observed,"earliest_holdout_date":None,
        "reason":"等待严格晚于全项目 max_observed_target_date 的未来新增交易日数据",
        "selection_data_reused":True,"untouched_holdout":False,"evidence_level":"sealed_retrospective_replay",
    }
    holdout_path=write_json_artifact(output/"future_untouched_holdout_plan.json",holdout,"future_untouched_holdout_plan","task_051_a")
    gaps=_gap_plan(raw_index,proof,suspension_blockers,args.index_code)
    gap_path=write_json_artifact(output/"targeted_backfill_plan.json",gaps,"targeted_backfill_plan","task_051_a")
    final={
        "status":"blocked","historical_snapshot_proof":bool(historical.historical_constituent_proof),
        "universe_mode":"daily_pit_constituents" if historical.historical_constituent_proof else "blocked",
        "alpha_discovery_data_ready":False,"research_holdout_firewall_enabled":False,
        "run_twenty_factor_validation":False,"certification_queue_count":0,"portfolio_queue_count":0,
        "paper_deployment_queue_count":0,"live_deployment_queue_count":0,
        "blockers":sorted(set(proof_blockers)),"snapshot_summary":{
            "snapshot_count":historical.snapshot_count,"union_member_count":historical.union_member_count,
            "usable_period":[historical.usable_start_date,historical.usable_end_date],"proof_manifest_path":historical.proof_manifest_path,
        },
        "paths":{"preflight_audit":str(preflight_path),"observation_ledger":str(ledger_path),"future_holdout_plan":str(holdout_path),"targeted_backfill_plan":str(gap_path)},
    }
    report_path=write_json_artifact(output/"task_051_engineering_report.json",final,"task_051_engineering_report","task_051_a")
    final["paths"]["engineering_report"]=str(report_path)
    return final


def _observation_ledger(campaign_root: Path,matrix_dir: Path)->list[dict[str,Any]]:
    dates=json.loads((matrix_dir/"trade_dates.json").read_text())
    max_date=max(map(str,dates)); min_date=min(map(str,dates)); rows=[]
    roots=[campaign_root,campaign_root.parent.parent/"validation_runs"]
    for root in roots:
        if not root.exists(): continue
        for path in sorted(root.rglob("*manifest*.json")):
            if path.stat().st_size>10_000_000: continue
            try: payload=json.loads(path.read_text())
            except: continue
            campaign_id=str(payload.get("campaign_id") or payload.get("validation_campaign_id") or path.parent.name)
            rows.append({"campaign_id":campaign_id,"artifact_path":str(path),"observed_target_start_date":min_date,"max_observed_target_date":max_date,"formula_candidate_influenced":True,"selection_data_reused":True,"untouched_holdout":False,"evidence_level":"contaminated_historical_replay"})
    return rows


def _gap_plan(raw_index:dict,proof:dict,suspension_blockers:list[str],index_code:str)->dict[str,Any]:
    return {"status":"planned_not_started","default_action":"do_not_blind_redownload","gaps":[
        {"dataset":"suspensions","index_code":index_code,"natural_month":"all_observed_history","missing_or_invalid_records":len(suspension_blockers),"required_schema":"trade_date/suspend_type/suspend_timing or complete suspend_date/resume_date interval","action":"governed resumable targeted backfill"},
        *({"dataset":"index_members","index_code":index_code,"natural_month":month,"missing_or_invalid_records":None,"action":"targeted natural-month index_weight backfill"} for month in proof.get("missing_months",[])),
    ],"credentials_required":True,"print_credentials":False,"source_index_hash":raw_index.get("index_hash")}


def _campaign_id(path:Path)->str:
    payload=json.loads(path.read_text()); return str(payload.get("campaign_id") or path.parent.name)
def _read_jsonl(path:Path)->list[dict[str,Any]]:
    with path.open() as handle:return [json.loads(line) for line in handle if line.strip()]
def _sha256(path:Path)->str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(8*1024*1024),b""):digest.update(chunk)
    return digest.hexdigest()
def _utc_now()->str:return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def _preflight_md(payload:dict)->str:
    lines=["# Task 051-A Preflight Audit","",f"- Status: `{payload['status']}`",f"- Dated suspension events: `{payload['dated_suspension_event_count']}`",f"- Unknown suspension events: `{payload['unknown_suspension_event_count']}`","","## Blockers"]
    lines.extend(f"- `{item}`" for item in payload["blockers"]); return "\n".join(lines)+"\n"


if __name__=="__main__":raise SystemExit(main())

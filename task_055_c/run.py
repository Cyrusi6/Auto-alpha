"""Native Task 055-C orchestrator. Success cannot be injected by callers."""
from __future__ import annotations
import argparse, json, os, shutil, tempfile
from pathlib import Path
from typing import Any, Mapping
from task_055_a.run import PHYSICAL_STATE_NAMES, PHYSICAL_STATE_TOKENS
from .cascade import build_cascade_plan, scan_caches
from .evidence import canonical_hash, build_truth_table, validate_truth_table
from .valuation import build_valuation_generation, validate_valuation_generation
from .fees import validate_fee_evidence

BLOCKED="task055c_evidence_acquisition_or_valuation_closure_blocked"
SIM_COMPLETE_RESEARCH_BLOCKED="task055c_simulator_engineering_completed_future_research_data_blocked_historical_selection_contaminated_execution_modeled_certification_blocked"
FULL_COMPLETE="task055c_simulator_engineering_completed_historical_selection_contaminated_execution_modeled_future_holdout_waiting_certification_blocked"

class Task055CError(RuntimeError): pass

def run(config:Mapping[str,Any])->dict[str,Any]:
    if config.get("simulation_replay_evidence") is not None: raise Task055CError("injected_simulation_replay_evidence_forbidden")
    dates=json.loads((Path(config["matrix_root"])/"trade_dates.json").read_text())
    truth=build_truth_table(inventory_manifest=config["inventory_manifest"],suspension_records=config["suspension_records"],suspension_coverage_ledger=config["suspension_coverage_ledger"],suspension_cache_root=config["suspension_cache_root"],output_root=Path(config["output_root"])/"truth",trade_dates=dates)
    plan=build_cascade_plan(truth_manifest=truth["manifest_path"],trade_dates=dates,output_root=Path(config["output_root"])/"cascade",max_transport_misses=int(config.get("max_transport_misses",2500)))
    cache=scan_caches(plan_manifest=plan["manifest_path"],cache_roots=list(config.get("cache_roots") or ()),output_root=Path(config["output_root"])/"cache_scan")
    valuation=build_valuation_generation(truth_manifest=truth["manifest_path"],matrix_root=config["matrix_root"],output_root=Path(config["output_root"])/"valuation")
    validate_valuation_generation(valuation["manifest_path"],truth_manifest=truth["manifest_path"],matrix_root=config["matrix_root"])
    replay=_verify_native_replay(config.get("simulation_run_root"))
    fees=validate_fee_evidence(config["fee_evidence_manifest"]) if config.get("fee_evidence_manifest") else {"status":"blocked","content_hash":None,"missing_rule_cells":[{"reason":"fee_evidence_manifest_missing"}]}
    queues=_inspect_operational_states(config["physical_state_roots"]); queue_counts={k:int(v["record_count"]) for k,v in queues.items()}
    if any(queue_counts.values()): raise Task055CError("downstream_physical_queue_nonempty")
    closure=valuation["unresolved_reporting_points"]==0 and plan["l3_authority_cases"]==[] and fees["status"]=="passed"
    if replay["verified"] and not closure: raise Task055CError("replay_exists_before_closure")
    full_research=truth["state_counts"].get("DATA_SOURCE_GAP",0)==0 and truth["state_counts"].get("CONFLICT",0)==0
    status=(FULL_COMPLETE if full_research else SIM_COMPLETE_RESEARCH_BLOCKED) if closure and replay["verified"] else BLOCKED
    report={"schema_version":"task055c_final_report_v1","status":status,"truth":_summary(truth),"cascade":_summary(plan),"cache_scan":_summary(cache),"valuation":_summary(valuation),"fee_evidence":{"content_hash":fees.get("content_hash"),"status":fees.get("status"),"missing_rule_cell_count":len(fees.get("missing_rule_cells") or ())},"replay":replay,"readiness":{"factor_replay_ready":True,"continuous_portfolio_valuation_ready":closure,"simulator_engineering_ready":closure and replay["verified"],"future_research_data_ready":full_research,"certification_ready":False,"portfolio_ready":False,"paper_ready":False,"live_ready":False},"queues":queue_counts,"prospective_holdout_accessed":False,"historical_selection_contaminated":True,"execution_evidence_level":"modeled_daily_bar_proxy","blockers":_blockers(truth,plan,cache,valuation,fees,replay)}
    return _publish(Path(config["output_root"])/"final",report)
def _verify_native_replay(path):
    if not path: return {"verified":False,"reason":"closure_gate_not_met_or_run_tree_missing"}
    root=Path(path); manifest=root/"task055c_native_replay_manifest.json"
    if not manifest.is_file(): return {"verified":False,"reason":"native_replay_manifest_missing"}
    payload=json.loads(manifest.read_text()); runs=payload.get("runs") or []; identities={(r.get("factor_id"),r.get("scenario_id")) for r in runs}
    if len(runs)!=100 or len(identities)!=100: raise Task055CError("native_replay_cartesian_invalid")
    return {"verified":True,"run_count":100,"content_hash":payload.get("content_hash")}
def _inspect_operational_states(state_roots):
    if set(state_roots) != set(PHYSICAL_STATE_NAMES): raise Task055CError("physical_state_roots_invalid")
    excluded={"validation_runs","campaigns","reports","freezes","matrix_cache","data","cache"}; result={}
    for name in PHYSICAL_STATE_NAMES:
        root=Path(state_roots[name]); tokens=PHYSICAL_STATE_TOKENS[name]; files=[]
        if root.exists():
            for path in root.rglob("*"):
                if not path.is_file() or path.name.endswith(".schema.json"): continue
                relative=path.relative_to(root)
                if relative.parts and relative.parts[0] in excluded: continue
                if all(token in path.name.lower() for token in tokens): files.append(path)
        records=0
        for path in files:
            try:
                if path.suffix in {".jsonl",".ndjson"}: records += sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())
                elif path.suffix==".json":
                    payload=json.loads(path.read_text(encoding="utf-8")); records += len(payload) if isinstance(payload,list) else int(bool(payload))
            except (OSError,UnicodeDecodeError,json.JSONDecodeError): raise Task055CError(f"physical_state_unreadable:{name}")
        result[name]={"root":str(root),"file_count":len(files),"record_count":records,"scan_scope":"operational_namespace_excluding_historical_artifacts"}
    return result
def _blockers(truth,plan,cache,valuation,fees,replay):
    result=[]
    if valuation["unresolved_reporting_points"]: result.append({"code":"valuation_reporting_points_unresolved","count":valuation["unresolved_reporting_points"]})
    necessary=sum(1 for r in plan["requests"] if r["stage"] in {"L1","L2"})
    if necessary and cache["transport_misses"]: result.append({"code":"governed_transport_requests_remaining","count":cache["transport_misses"]})
    if plan["l3_authority_cases"]: result.append({"code":"authority_evidence_cases_remaining","count":len(plan["l3_authority_cases"])})
    if fees["status"] != "passed": result.append({"code":"governed_fee_rule_coverage_incomplete","count":len(fees.get("missing_rule_cells") or ())})
    if not replay["verified"]: result.append({"code":"native_simulator_replay_not_started"})
    result += [{"code":c} for c in ("historical_selection_contamination","execution_modeled","suspension_timing_semantics_uncertified","constituent_publication_timing_unknown","vendor_historical_revision_risk","prospective_holdout_not_arrived")]
    return result
def _summary(x): return {k:x.get(k) for k in ("content_hash","manifest_path","record_count","episode_count","state_counts","reconciliation","valuation_domain_count","transport_request_count","l1_stock_count","l2_stock_count","cache_hits","transport_misses","reporting_points","covered_reporting_points","unresolved_reporting_points","status") if k in x}
def _publish(root,report):
    root.mkdir(parents=True,exist_ok=True); content=canonical_hash(report); generation=f"task055c_result_{content[:24]}"; payload=dict(report,content_hash=content,generation_id=generation); target=root/"generations"/generation; target.mkdir(parents=True,exist_ok=True); path=target/"task055c_final_report.json"; path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); return payload|{"manifest_path":str(path)}
def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--config",required=True); a=p.parse_args(argv); print(json.dumps(run(json.loads(Path(a.config).read_text())),indent=2,sort_keys=True))
if __name__=="__main__": main()

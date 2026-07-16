"""Sequential L0/L1 planning with valuation-anchor remediation."""
from __future__ import annotations
import json, os, tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import numpy as np
from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_c.evidence import validate_truth_table
from .cache import evidence_use_identity, transport_identity
from .contracts import DAILY_FIELDS, GLOBAL_BUDGET, MAX_DATE

class PlanError(RuntimeError): pass

def build_l0_l1_plan(*,parent_truth_manifest:str|Path,parent_valuation_manifest:str|Path,matrix_root:str|Path,output_root:str|Path)->dict[str,Any]:
    truth=validate_truth_table(parent_truth_manifest); valuation_path=Path(parent_valuation_manifest); valuation=json.loads(valuation_path.read_text()); valuation_root=valuation_path.parent
    keys=json.loads((valuation_root/valuation["partitions"]["keys"]["path"]).read_text()); methods=np.load(valuation_root/valuation["partitions"]["methods"]["path"],mmap_mode="r"); source=np.load(valuation_root/valuation["partitions"]["source_date"]["path"],mmap_mode="r"); stale=np.load(valuation_root/valuation["partitions"]["stale_age"]["path"],mmap_mode="r")
    truth_by_key={(row["ts_code"],row["trade_date"]):row for row in truth["records"]}; matrix=Path(matrix_root); dates=json.loads((matrix/"trade_dates.json").read_text()); date_index={date:i for i,date in enumerate(dates)}
    causes=Counter(); anchor_rows=[]
    for index,key in enumerate(keys):
        row=truth_by_key[tuple(key)]
        if row["state"]!="VENDOR_DAILY_NON_TRADING_MODELED" or np.all(methods[index]!=0): continue
        if np.all(source[index]<0): cause="no_prior_authoritative_close"
        elif np.max(stale[index])>int(valuation["max_stale_age_trade_days"]): cause="stale_age_exceeds_policy"
        elif row["daily_bar"]=="absent" and any((row.get("raw_field_validity") or {}).values()): cause="matrix_truth_conflict"
        elif row.get("corporate_action") is None and not row.get("active",True): cause="lifecycle_or_corporate_action_anchor_missing"
        else: cause="other_anchor_failure"
        causes[cause]+=1; anchor_rows.append({"ts_code":key[0],"trade_date":key[1],"cause":cause,"evidence_hash":row["evidence_hash"]})
    unresolved=[row for row in truth["records"] if row["valuation_domain_intersection"] and row["state"] in {"DATA_SOURCE_GAP","CONFLICT","TRADED_SOURCE_CONFLICT"}]
    remediation=unresolved+anchor_rows; by_stock=defaultdict(list)
    for row in remediation: by_stock[row["ts_code"]].append(row)
    if len(by_stock)>113: raise PlanError(f"l1_initial_stock_limit_exceeded:{len(by_stock)}")
    requests=[]
    plan_basis_hash=stable_json_hash({"parent_truth_hash":truth["content_hash"],"parent_valuation_hash":valuation["content_hash"],"max_date":MAX_DATE,"remediation_keys":sorted(f"{row['ts_code']}|{row['trade_date']}" for row in remediation)})
    for code,rows in sorted(by_stock.items()):
        positions=[date_index[row["trade_date"]] for row in rows]; params={"ts_code":code,"start_date":dates[max(0,min(positions)-1)],"end_date":dates[min(len(dates)-1,max(positions)+1)]}; transport=transport_identity("daily",params,DAILY_FIELDS); key_hash=stable_json_hash(sorted(f"{row['ts_code']}|{row['trade_date']}" for row in rows)); requests.append({"stage":"L1","api_name":"daily","params":params,"fields":list(DAILY_FIELDS),"transport_hash":transport,"valuation_key_hash":key_hash,"evidence_use_hash":evidence_use_identity(task="task_055_d",stage="L1",parent_plan_hash=plan_basis_hash,valuation_key_hash=key_hash,transport_hash=transport)})
    semantic={"schema_version":"task055d_l0_l1_plan_v1","status":"sealed","parent_truth_hash":truth["content_hash"],"parent_valuation_hash":valuation["content_hash"],"plan_basis_hash":plan_basis_hash,"max_date":MAX_DATE,"global_network_budget":GLOBAL_BUDGET,"unresolved_evidence_cells":len(unresolved),"modeled_unmarked_cells":len(anchor_rows),"anchor_cause_counts":dict(sorted(causes.items())),"anchor_rows":anchor_rows,"l1_stock_count":len(requests),"requests":requests,"prospective_holdout_boundary":MAX_DATE}
    content=stable_json_hash(semantic); payload=semantic|{"content_hash":content,"generation_id":f"l0_l1_plan_{content[:24]}"}; root=Path(output_root); root.mkdir(parents=True,exist_ok=True); path=root/f"{payload['generation_id']}.json"; path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); return payload|{"manifest_path":str(path)}

def build_l2_child_plan(*,l1_plan:dict[str,Any],l1_reconciliation:dict[str,Any],remaining_rows:list[dict[str,Any]],output_root:str|Path)->dict[str,Any]:
    if l1_reconciliation.get("status")!="applied": raise PlanError("l2_requires_applied_l1_reconciliation")
    resolved=set(l1_reconciliation.get("resolved_keys") or ()); by_stock=defaultdict(list)
    for row in remaining_rows:
        key=f"{row['ts_code']}|{row['trade_date']}"
        if key not in resolved: by_stock[row["ts_code"]].append(row)
    from .contracts import SUSPEND_FIELDS
    requests=[]
    for code,rows in sorted(by_stock.items()):
        params={"ts_code":code,"start_date":min(row["trade_date"] for row in rows),"end_date":max(row["trade_date"] for row in rows)}; transport=transport_identity("suspend_d",params,SUSPEND_FIELDS); key_hash=stable_json_hash(sorted(f"{row['ts_code']}|{row['trade_date']}" for row in rows)); requests.append({"stage":"L2","api_name":"suspend_d","params":params,"fields":list(SUSPEND_FIELDS),"transport_hash":transport,"valuation_key_hash":key_hash,"evidence_use_hash":evidence_use_identity(task="task_055_d",stage="L2",parent_plan_hash=l1_reconciliation["content_hash"],valuation_key_hash=key_hash,transport_hash=transport)})
    semantic={"schema_version":"task055d_l2_child_plan_v1","status":"sealed","parent_l1_plan_hash":l1_plan["content_hash"],"l1_result_hash":l1_reconciliation["content_hash"],"requests":requests,"request_count":len(requests)}; content=stable_json_hash(semantic); payload=semantic|{"content_hash":content}; path=Path(output_root)/f"l2_plan_{content[:24]}.json"; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); return payload|{"manifest_path":str(path)}

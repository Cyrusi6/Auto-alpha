"""Cache-first bounded L0-L3 evidence acquisition planning."""
from __future__ import annotations
import json, os, shutil, tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping
from data_pipeline.ashare.request_normalization import normalize_tushare_request, stable_json_hash
from .evidence import MAX_DATE, canonical_hash, sha256_file, validate_truth_table

SCHEMA="task055c_cascade_request_plan_v1"
MAX_TRANSPORT_MISSES=2500
DAILY_FIELDS=("ts_code","trade_date","open","high","low","close","pre_close","vol","amount")
SUSPEND_FIELDS=("ts_code","trade_date","suspend_timing","suspend_type")

class CascadeError(RuntimeError): pass

def build_cascade_plan(*, truth_manifest:str|Path, trade_dates:list[str], output_root:str|Path, max_transport_misses:int=MAX_TRANSPORT_MISSES)->dict[str,Any]:
    if max_transport_misses>MAX_TRANSPORT_MISSES or max_transport_misses<0: raise CascadeError("transport_budget_invalid")
    truth=validate_truth_table(truth_manifest); dates=sorted(str(d) for d in trade_dates if str(d)<=MAX_DATE); index={d:i for i,d in enumerate(dates)}
    required=[r for r in truth["records"] if r["valuation_domain_intersection"] and r["state"] in {"DATA_SOURCE_GAP","CONFLICT","TRADED_SOURCE_CONFLICT"}]
    by_stock=defaultdict(list)
    for row in required: by_stock[row["ts_code"]].append(row)
    requests=[]
    for code,rows in sorted(by_stock.items()):
        positions=[index[r["trade_date"]] for r in rows if r["trade_date"] in index]
        if not positions: continue
        start=dates[max(0,min(positions)-1)]; end=dates[min(len(dates)-1,max(positions)+1)]
        requests.append(_request("L1","daily",{"ts_code":code,"start_date":start,"end_date":end},DAILY_FIELDS,valuation_keys=[f"{r['ts_code']}|{r['trade_date']}" for r in rows]))
    l2_rows=[r for r in required if r["suspend_type"]=="none"]
    for code, rows in sorted(_merge_by_stock(l2_rows).items()):
        requests.append(_request("L2","suspend_d",{"ts_code":code,"start_date":rows[0]["trade_date"],"end_date":rows[-1]["trade_date"]},SUSPEND_FIELDS,valuation_keys=[f"{r['ts_code']}|{r['trade_date']}" for r in rows]))
    l3=[{"ts_code":r["ts_code"],"trade_date":r["trade_date"],"reason":r["reason_code"]} for r in required if r["suspend_type"] in {"R","S+R"} or r["suspend_timing"] in {"blank","explicit-intraday","unparsed"} or r["lifecycle_corporate_action_conflict"] or r["regression_probe"]]
    transport={r["transport_hash"]:r for r in requests}
    if len(transport)>max_transport_misses: raise CascadeError(f"planned_transport_misses_exceed_budget:{len(transport)}")
    semantic={"schema_version":SCHEMA,"status":"published","truth_content_hash":truth["content_hash"],"max_date":MAX_DATE,"max_transport_misses":max_transport_misses,"valuation_required_unresolved":len(required),"l1_stock_count":sum(r["stage"]=="L1" for r in transport.values()),"l2_stock_count":sum(r["stage"]=="L2" for r in transport.values()),"transport_request_count":len(transport),"requests":sorted(transport.values(),key=lambda r:(r["stage"],r["api_name"],r["transport_hash"])),"l3_authority_cases":l3,"document_download_budget":20,"prospective_holdout_access_allowed":False}
    return _publish(Path(output_root),semantic)

def scan_caches(*, plan_manifest:str|Path, cache_roots:list[str|Path], output_root:str|Path)->dict[str,Any]:
    plan=json.loads(Path(plan_manifest).read_text()); found={}; invalid=[]
    for root in map(Path,cache_roots):
        if not root.exists(): continue
        for path in root.rglob("*.json"):
            try: envelope=json.loads(path.read_text()); request=envelope.get("request") or {}; transport=_transport_hash(request.get("api_name"),request.get("params") or {},request.get("fields") or ())
            except Exception: continue
            if transport not in {r["transport_hash"] for r in plan["requests"]} or transport in found: continue
            try: _verify_envelope(envelope,plan_request=next(r for r in plan["requests"] if r["transport_hash"]==transport)); found[transport]={"path":str(path),"sha256":sha256_file(path),"item_count":len(envelope.get("records") or ())}
            except CascadeError as exc: invalid.append({"transport_hash":transport,"path":str(path),"reason":str(exc)})
    misses=[r for r in plan["requests"] if r["transport_hash"] not in found]
    semantic={"schema_version":"task055c_l0_cache_scan_v1","status":"complete","plan_content_hash":plan["content_hash"],"planned":len(plan["requests"]),"cache_hits":len(found),"transport_misses":len(misses),"hits":found,"misses":misses,"invalid_cache_entries":invalid,"all_planned_items_scanned":True}
    return _publish(Path(output_root),semantic,prefix="l0_cache_scan")

def _request(stage,api,params,fields,valuation_keys):
    normalized=normalize_tushare_request(api,params=params,fields=fields); transport=_transport_hash(api,normalized["params"],normalized["fields"]); use={"task":"task_055_c","stage":stage,"valuation_domain_hash":stable_json_hash(sorted(valuation_keys)),"transport_hash":transport}
    return {"stage":stage,"api_name":api,"normalized_params":normalized["params"],"fields":normalized["fields"],"transport_hash":transport,"evidence_use_hash":stable_json_hash(use),"valuation_key_count":len(valuation_keys),"valuation_key_hash":stable_json_hash(sorted(valuation_keys))}
def _transport_hash(api,params,fields): return stable_json_hash({"endpoint_api":api,"provider_api_version":"tushare_pro_http.v1","normalized_params":dict(params),"fields":list(fields)})
def _merge_by_stock(rows):
    result=defaultdict(list)
    for row in sorted(rows,key=lambda r:(r["ts_code"],r["trade_date"])): result[row["ts_code"]].append(row)
    return result
def _verify_envelope(envelope,plan_request):
    if envelope.get("schema_version")!="tushare_cache_envelope.v2": raise CascadeError("cache_schema_invalid")
    req=envelope.get("request") or {}; resp=envelope.get("response") or {}; records=envelope.get("records") or []
    if _transport_hash(req.get("api_name"),req.get("params") or {},req.get("fields") or ())!=plan_request["transport_hash"]: raise CascadeError("cache_transport_identity_mismatch")
    if resp.get("code")!=0 or resp.get("complete") is not True or resp.get("item_count")!=len(records) or stable_json_hash(records)!=resp.get("records_sha256"): raise CascadeError("cache_response_integrity_invalid")
    if len(records)>=1000: raise CascadeError("cache_response_capped")
def _publish(root,semantic,prefix="cascade_plan"):
    root.mkdir(parents=True,exist_ok=True); content=canonical_hash(semantic); generation=f"{prefix}_{content[:24]}"; payload=semantic|{"content_hash":content,"generation_id":generation}; target=root/"generations"/generation; target.mkdir(parents=True,exist_ok=True); path=target/f"{prefix}.json"
    if path.exists() and json.loads(path.read_text())!=payload: raise CascadeError("immutable_generation_conflict")
    path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); return payload|{"manifest_path":str(path)}

def execute_transport_stage(*,plan_manifest:str|Path,output_root:str|Path,stage:str,request_budget:int,requests_per_minute:float=120.0)->dict[str,Any]:
    """Execute one immutable cascade stage, scanning every item before spending budget."""
    import os
    from data_pipeline.ashare.config import AShareDataConfig
    from data_pipeline.ashare.providers.tushare_client import TushareHttpClient
    from data_pipeline.ashare.rate_limit import RequestRateLimitConfig, SimpleRateLimiter
    plan=json.loads(Path(plan_manifest).read_text()); selected=[r for r in plan["requests"] if r["stage"]==stage]
    if request_budget<0 or request_budget>MAX_TRANSPORT_MISSES: raise CascadeError("execution_budget_invalid")
    root=Path(output_root); response_root=root/"responses"; response_root.mkdir(parents=True,exist_ok=True)
    executions=[]; misses=[]
    for request in selected:
        path=response_root/f"{request['transport_hash']}.json"
        if path.is_file():
            envelope=json.loads(path.read_text()); _verify_envelope(envelope,request); executions.append({"transport_hash":request["transport_hash"],"cache_hit":True,"path":str(path),"sha256":sha256_file(path),"item_count":len(envelope.get("records") or ())})
        else: misses.append(request)
    client=None; network=0
    for request in misses:
        if network>=request_budget: continue
        if client is None:
            config=AShareDataConfig.from_env()
            if not config.tushare_token: raise CascadeError("tushare_token_unavailable")
            limiter=SimpleRateLimiter(RequestRateLimitConfig(requests_per_minute=requests_per_minute,burst_size=1,enabled=True))
            client=TushareHttpClient(config,rate_limiter=limiter)
        response=client.post_with_metadata(request["api_name"],params=dict(request["normalized_params"]),fields=tuple(request["fields"]))
        records=[dict(row) for row in response.records]
        if len(records)>=6000: raise CascadeError(f"response_capped_requires_split:{request['transport_hash']}")
        envelope={"schema_version":"tushare_cache_envelope.v2","code_semantic_hash":response.code_semantic_hash,"request":{"api_name":request["api_name"],"params":dict(request["normalized_params"]),"fields":list(request["fields"]),"version":"tushare_request.v1"},"request_fingerprint":request["transport_hash"],"records":records,"response":{"code":response.response_code,"complete":True,"fields":list(response.response_fields),"item_count":len(records),"message":response.response_message,"records_sha256":stable_json_hash(records)},"metadata":{"api_name":request["api_name"],"records":len(records),"endpoint":response.endpoint,"provider_api_version":response.provider_api_version}}
        path=response_root/f"{request['transport_hash']}.json"; temp=path.with_name(f".{path.name}.tmp"); temp.write_text(json.dumps(envelope,indent=2,sort_keys=True)+"\n"); os.replace(temp,path); _verify_envelope(envelope,request); network+=1; executions.append({"transport_hash":request["transport_hash"],"cache_hit":False,"path":str(path),"sha256":sha256_file(path),"item_count":len(records)})
    completed={r["transport_hash"] for r in executions}; remaining=[r for r in selected if r["transport_hash"] not in completed]
    semantic={"schema_version":"task055c_transport_execution_v1","status":"complete" if not remaining else "budget_exhausted","plan_content_hash":plan["content_hash"],"stage":stage,"planned":len(selected),"completed":len(executions),"cache_hits":sum(r["cache_hit"] for r in executions),"transport_misses_executed":network,"remaining":len(remaining),"executions":sorted(executions,key=lambda r:r["transport_hash"]),"remaining_requests":remaining,"prospective_holdout_accessed":False}
    return _publish(root/"runs",semantic,prefix=f"{stage.lower()}_execution")

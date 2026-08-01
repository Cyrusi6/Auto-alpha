"""Read-only v2/v3 inventory and validated v3 response publication."""
from __future__ import annotations
import json, os, shutil, tempfile
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from data_pipeline.ashare.cache import (
    CACHE_ENVELOPE_VERSION,
    LEGACY_CACHE_ENVELOPE_VERSION,
    TushareResponseCache,
    tushare_cache_source_hash,
)
from data_pipeline.ashare.request_normalization import (
    normalize_tushare_request,
    stable_json_hash,
    tushare_code_semantic_hash,
    tushare_request_fingerprint,
)
from data_pipeline.ashare.validators import is_valid_ts_code, is_valid_yyyymmdd
from .contracts import (
    CANONICAL_ORIGIN,
    ENDPOINT_CAPS,
    MAX_DATE,
    PROVIDER_API_VERSION,
    REQUEST_NORMALIZATION_VERSION,
)

class SecureCacheError(RuntimeError): pass

def transport_identity(api_name:str,params:Mapping[str,Any],fields:Iterable[str])->str:
    return stable_json_hash({"origin":CANONICAL_ORIGIN,"provider_api_version":PROVIDER_API_VERSION,"request_normalization_version":REQUEST_NORMALIZATION_VERSION,"request":normalize_tushare_request(api_name,params=dict(params),fields=fields)})

def evidence_use_identity(*,task:str,stage:str,parent_plan_hash:str,valuation_key_hash:str,transport_hash:str)->str:
    return stable_json_hash({"task":task,"stage":stage,"parent_plan_hash":parent_plan_hash,"valuation_key_hash":valuation_key_hash,"transport_hash":transport_hash})

def inventory_caches(cache_roots:Iterable[str|Path],logical_requests:Iterable[Mapping[str,Any]])->dict[str,Any]:
    requests={str(row["transport_hash"]):dict(row) for row in logical_requests}; candidates=[]; hits={}; invalid=[]
    for root in map(Path,cache_roots):
        if not root.exists(): continue
        for path in root.rglob("*.json"):
            try: payload=json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc: invalid.append({"path":str(path),"reason":"unreadable_json"}); continue
            version=payload.get("schema_version"); request=payload.get("request") or {}
            if version not in {CACHE_ENVELOPE_VERSION,LEGACY_CACHE_ENVELOPE_VERSION}: continue
            identity=transport_identity(str(request.get("api_name")),request.get("params") or {},request.get("fields") or ())
            matched=identity in requests; item={"path":str(path),"schema_version":version,"physical_sha256":_sha(path),"transport_hash":identity,"transport_identity_match":matched}
            candidates.append(item)
            if not matched: continue
            try:
                _validate_physical_payload(payload,requests[identity]); hits.setdefault(identity,item)
            except SecureCacheError as exc: invalid.append(item|{"reason":str(exc)})
    return {"physical_candidates":len(candidates),"transport_identity_matches":sum(row["transport_identity_match"] for row in candidates),"validated_hits":len(hits),"hits":hits,"invalid_entries":invalid,"inventory_hash":stable_json_hash({"candidates":candidates,"hits":hits,"invalid":invalid})}

def find_endpoint_schema_proof(cache_roots:Iterable[str|Path],api_name:str,fields:Iterable[str])->dict[str,Any]|None:
    requested=list(normalize_tushare_request(api_name,fields=fields)["fields"]); proofs=[]
    for root in map(Path,cache_roots):
        if not root.exists(): continue
        for path in root.rglob("*.json"):
            try: payload=json.loads(path.read_text(encoding="utf-8"))
            except Exception: continue
            request=payload.get("request") or {}; response=payload.get("response") or {}; records=payload.get("records") or []
            if payload.get("schema_version")!=CACHE_ENVELOPE_VERSION or request.get("api_name")!=api_name or request.get("fields")!=requested: continue
            if response.get("code")!=0 or not records or response.get("item_count")!=len(records) or not set(requested).issubset(response.get("fields") or ()): continue
            proof={"api_name":api_name,"requested_fields":requested,"response_fields":response["fields"],"code_semantic_hash":payload.get("code_semantic_hash"),"source_cache_sha256":_sha(path),"source_request_fingerprint":payload.get("request_fingerprint")}; proof["proof_hash"]=stable_json_hash(proof); proofs.append(proof)
    return min(proofs,key=lambda row:(row["source_cache_sha256"],row["source_request_fingerprint"])) if proofs else None

def publish_validated_response(*,cache_root:str|Path,request:Mapping[str,Any],envelope:Any,endpoint_schema_proof:dict[str,Any]|None=None)->dict[str,Any]:
    records=[dict(row) for row in envelope.records]
    response_fields=list(envelope.response_fields)
    observed=bool(response_fields)
    if not records and not set(request["fields"]).issubset(response_fields):
        if not endpoint_schema_proof or not set(request["fields"]).issubset(endpoint_schema_proof.get("response_fields") or ()):
            raise SecureCacheError("negative_attestation_schema_proof_missing")
        response_fields=list(endpoint_schema_proof["response_fields"])
    staged={"api_name":request["api_name"],"params":dict(request["params"]),"fields":tuple(request["fields"]),"records":records,"response_code":envelope.response_code,"response_message":envelope.response_message,"response_fields":response_fields,"item_count":envelope.item_count,"response_fields_observed":observed,"endpoint_schema_proof":endpoint_schema_proof,"endpoint":envelope.endpoint,"provider_api_version":envelope.provider_api_version}
    _validate_records(request,records,response_fields)
    target=TushareResponseCache(cache_root,enabled=True)
    path=target.write(**staged)
    result=target.read(request["api_name"],params=dict(request["params"]),fields=tuple(request["fields"]),endpoint_schema_proof=endpoint_schema_proof,allow_legacy_source_semantics=False)
    if not result or not result.hit: raise SecureCacheError("published_v3_cache_not_readable")
    return {"path":str(path),"sha256":_sha(path),"item_count":len(records),"schema_version":CACHE_ENVELOPE_VERSION}

def _validate_physical_payload(payload,request):
    provider=payload.get("provider") or {}; response=payload.get("response") or {}; records=payload.get("records") or []
    if payload.get("schema_version")!=CACHE_ENVELOPE_VERSION: raise SecureCacheError("legacy_v2_not_formal_hit")
    if provider.get("endpoint")!=CANONICAL_ORIGIN or provider.get("api_version")!=PROVIDER_API_VERSION: raise SecureCacheError("provider_origin_or_version_invalid")
    normalized=normalize_tushare_request(request["api_name"],params=request["params"],fields=request["fields"])
    if payload.get("request")!=normalized: raise SecureCacheError("normalized_request_mismatch")
    if payload.get("request_fingerprint")!=tushare_request_fingerprint(request["api_name"],params=request["params"],fields=request["fields"]): raise SecureCacheError("request_fingerprint_mismatch")
    if payload.get("code_semantic_hash")!=tushare_code_semantic_hash() or payload.get("source_code_hash")!=tushare_cache_source_hash(): raise SecureCacheError("cache_code_semantic_mismatch")
    if response.get("complete") is not True or response.get("item_count")!=len(records): raise SecureCacheError("response_count_or_complete_invalid")
    if stable_json_hash(records)!=response.get("records_sha256"): raise SecureCacheError("response_hash_invalid")
    if not records and response.get("fields_observed") is not True:
        proof=payload.get("endpoint_schema_proof") or {}
        unsigned={key:value for key,value in proof.items() if key!="proof_hash"}
        if proof.get("proof_hash")!=stable_json_hash(unsigned) or not set(request["fields"]).issubset(proof.get("response_fields") or ()): raise SecureCacheError("negative_attestation_schema_proof_missing")
    _validate_records(request,records,response.get("fields") or ())

def split_capped_request(request:Mapping[str,Any],trade_dates:list[str])->list[dict[str,Any]]:
    start=str(request["params"].get("start_date") or ""); end=str(request["params"].get("end_date") or "")
    window=[date for date in trade_dates if (not start or date>=start) and (not end or date<=end)]
    if len(window)<2: raise SecureCacheError("endpoint_cap_reached_unsplittable")
    midpoint=len(window)//2; ranges=((window[0],window[midpoint-1]),(window[midpoint],window[-1])); children=[]
    for child_start,child_end in ranges:
        params=dict(request["params"]); params.update({"start_date":child_start,"end_date":child_end}); child=dict(request); child["params"]=params; child["transport_hash"]=transport_identity(request["api_name"],params,request["fields"]); child["parent_transport_hash"]=request["transport_hash"]; children.append(child)
    return children
def _validate_records(request,records,response_fields):
    fields=tuple(request["fields"])
    if not set(fields).issubset(set(response_fields)): raise SecureCacheError("response_fields_missing")
    cap=ENDPOINT_CAPS[request["api_name"]]
    if len(records)>=cap: raise SecureCacheError("endpoint_cap_reached_split_required")
    seen=set(); params=request["params"]; start=params.get("start_date"); end=params.get("end_date"); code=params.get("ts_code"); exact=params.get("trade_date")
    for row in records:
        row_code=str(row.get("ts_code")); date=str(row.get("trade_date"))
        if not is_valid_ts_code(row_code) or not is_valid_yyyymmdd(date): raise SecureCacheError("response_code_or_date_invalid")
        if code and row_code!=code: raise SecureCacheError("response_wrong_code")
        if date>MAX_DATE or (exact and date!=exact) or (start and date<start) or (end and date>end): raise SecureCacheError("response_date_outside_geometry")
        if request["api_name"]=="daily":
            key=(row_code,date)
            values={name:_number(row.get(name),name) for name in fields[2:]}
            if any(values[name]<=0 for name in ("open","high","low","close","pre_close")) or values["high"]<max(values["low"],values["open"],values["close"]) or values["low"]>min(values["high"],values["open"],values["close"]) or values["vol"]<0 or values["amount"]<0: raise SecureCacheError("daily_numeric_contract_invalid")
        else:
            kind=str(row.get("suspend_type")); timing=row.get("suspend_timing")
            if kind not in {"S","R"} or not _valid_timing(timing): raise SecureCacheError("suspension_contract_invalid")
            key=(row_code,date,kind,timing)
        if key in seen: raise SecureCacheError("response_primary_key_duplicate")
        seen.add(key)
def _number(value,name):
    try: number=float(value)
    except Exception as exc: raise SecureCacheError(f"daily_non_numeric:{name}") from exc
    if number!=number or number in (float("inf"),float("-inf")): raise SecureCacheError(f"daily_non_finite:{name}")
    return number
def _valid_timing(value):
    if value is None: return True
    if not isinstance(value,str): return False
    if not value.strip(): return True
    return all(re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}",part.strip()) for part in value.split(","))
def _sha(path):
    import hashlib
    h=hashlib.sha256(); h.update(Path(path).read_bytes()); return h.hexdigest()

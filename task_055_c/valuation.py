"""Full-axis valuation state machine for Task 055-C."""
from __future__ import annotations
import json, os, shutil, tempfile
from pathlib import Path
from typing import Any
import numpy as np
from .evidence import MODELED, canonical_hash, sha256_file, validate_truth_table

SCHEMA="task055c_chunked_valuation_v1"
METHODS={"UNRESOLVED":0,"OFFICIAL_OPEN":1,"OFFICIAL_CLOSE":2,"STALE_OFFICIAL_NON_TRADING":3,"STALE_VENDOR_DAILY_NON_TRADING_MODELED":4,"LIFECYCLE_SETTLEMENT":5}

class ValuationClosureError(RuntimeError): pass

def build_valuation_generation(*, truth_manifest:str|Path, matrix_root:str|Path, output_root:str|Path, max_stale_age_trade_days:int=250)->dict[str,Any]:
    truth=validate_truth_table(truth_manifest); matrix=Path(matrix_root); manifest=json.loads((matrix/"task_052a_strict_matrix_manifest.json").read_text())
    codes=json.loads((matrix/"ts_codes.json").read_text()); dates=json.loads((matrix/"trade_dates.json").read_text()); shape=(len(codes),len(dates)); ci={c:i for i,c in enumerate(codes)}; di={d:i for i,d in enumerate(dates)}
    close=np.load(matrix/"close.npy",mmap_mode="r"); close_valid=np.load(matrix/"close_validity.npy",mmap_mode="r"); open_=np.load(matrix/"open.npy",mmap_mode="r"); open_valid=np.load(matrix/"open_validity.npy",mmap_mode="r")
    if close.shape!=shape or open_.shape!=shape: raise ValuationClosureError("matrix_axis_shape_mismatch")
    required=[r for r in truth["records"] if r["valuation_domain_intersection"]]; required.sort(key=lambda r:(r["trade_date"],r["ts_code"])); key_index={(r["ts_code"],r["trade_date"]):i for i,r in enumerate(required)}
    values=np.full((len(required),2),np.nan,dtype=np.float64); methods=np.zeros((len(required),2),dtype=np.uint8); source_date=np.full((len(required),2),-1,dtype=np.int32); evidence_id=np.zeros((len(required),2),dtype="S64"); stale_age=np.zeros((len(required),2),dtype=np.int32)
    last_price=np.full(len(codes),np.nan,dtype=np.float64); last_date=np.full(len(codes),-1,dtype=np.int32)
    truth_by_key={(r["ts_code"],r["trade_date"]):r for r in required}
    for date_pos,date in enumerate(dates):
        valid=np.asarray(close_valid[:,date_pos],dtype=bool); last_price[valid]=np.asarray(close[:,date_pos],dtype=np.float64)[valid]; last_date[valid]=date_pos
        for code in codes:
            row=truth_by_key.get((code,date))
            if row is None: continue
            out=key_index[(code,date)]; asset=ci[code]; eid=row["evidence_hash"].encode()
            if row["daily_bar"]=="present-complete":
                if bool(open_valid[asset,date_pos]) and bool(close_valid[asset,date_pos]):
                    values[out]=[float(open_[asset,date_pos]),float(close[asset,date_pos])]; methods[out]=[METHODS["OFFICIAL_OPEN"],METHODS["OFFICIAL_CLOSE"]]; source_date[out]=date_pos; evidence_id[out]=eid
                continue
            if row["state"]==MODELED and np.isfinite(last_price[asset]) and 0 <= date_pos-last_date[asset] <= max_stale_age_trade_days:
                values[out]=last_price[asset]; methods[out]=METHODS["STALE_VENDOR_DAILY_NON_TRADING_MODELED"]; source_date[out]=last_date[asset]; evidence_id[out]=eid; stale_age[out]=date_pos-last_date[asset]
    unresolved=int(np.count_nonzero(methods==0)); covered=int(methods.size-unresolved)
    return _publish(Path(output_root),truth,manifest,codes,dates,required,values,methods,source_date,evidence_id,stale_age,covered,unresolved,max_stale_age_trade_days)

def validate_valuation_generation(path:str|Path,*,truth_manifest:str|Path,matrix_root:str|Path,recompute:bool=True)->dict[str,Any]:
    p=Path(path); manifest_path=p if p.is_file() else p/"valuation_manifest.json"; manifest=json.loads(manifest_path.read_text()); root=manifest_path.parent
    if manifest.get("schema_version")!=SCHEMA: raise ValuationClosureError("valuation_schema_invalid")
    for entry in manifest["partitions"].values():
        artifact=root/entry["path"]
        if not artifact.is_file() or sha256_file(artifact)!=entry["sha256"]: raise ValuationClosureError("valuation_partition_mismatch")
    if recompute:
        temp=Path(tempfile.mkdtemp(prefix="task055c_verify."))
        try:
            rebuilt=build_valuation_generation(truth_manifest=truth_manifest,matrix_root=matrix_root,output_root=temp,max_stale_age_trade_days=manifest["max_stale_age_trade_days"])
            for name in ("values","methods","source_date","evidence_id","stale_age"):
                if rebuilt["partitions"][name]["sha256"]!=manifest["partitions"][name]["sha256"]: raise ValuationClosureError(f"valuation_recompute_mismatch:{name}")
        finally: shutil.rmtree(temp,ignore_errors=True)
    semantic={k:v for k,v in manifest.items() if k not in {"content_hash","generation_id"}}
    if canonical_hash(semantic)!=manifest["content_hash"]: raise ValuationClosureError("valuation_content_hash_mismatch")
    return manifest|{"manifest_path":str(manifest_path)}

def _publish(root,truth,matrix_manifest,codes,dates,required,values,methods,source_date,evidence_id,stale_age,covered,unresolved,max_age):
    root.mkdir(parents=True,exist_ok=True); staging=Path(tempfile.mkdtemp(prefix=".task055c_valuation.",dir=root))
    try:
        np.save(staging/"mark_values.npy",values); np.save(staging/"mark_methods.npy",methods); np.save(staging/"mark_source_date_index.npy",source_date); np.save(staging/"mark_evidence_id.npy",evidence_id); np.save(staging/"stale_age_trade_days.npy",stale_age)
        (staging/"valuation_keys.json").write_text(json.dumps([[r["ts_code"],r["trade_date"]] for r in required],separators=(",",":"))+"\n")
        files={"values":"mark_values.npy","methods":"mark_methods.npy","source_date":"mark_source_date_index.npy","evidence_id":"mark_evidence_id.npy","stale_age":"stale_age_trade_days.npy","keys":"valuation_keys.json"}; parts={k:{"path":v,"sha256":sha256_file(staging/v),"size_bytes":(staging/v).stat().st_size} for k,v in files.items()}
        semantic={"schema_version":SCHEMA,"status":"passed" if unresolved==0 else "blocked","truth_content_hash":truth["content_hash"],"matrix_content_hash":matrix_manifest.get("content_hash"),"stock_axis_hash":matrix_manifest.get("fingerprints",{}).get("stock_axis_hash"),"date_axis_hash":matrix_manifest.get("fingerprints",{}).get("date_axis_hash"),"valuation_domain_cells":len(required),"reporting_points":len(required)*2,"covered_reporting_points":covered,"unresolved_reporting_points":unresolved,"illegal_carry_count":0,"max_stale_age_trade_days":max_age,"method_codes":METHODS,"partitions":parts}
        content=canonical_hash(semantic); generation=f"valuation_{content[:24]}"; payload=semantic|{"content_hash":content,"generation_id":generation}; (staging/"valuation_manifest.json").write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); target=root/"generations"/generation; target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): shutil.rmtree(staging)
        else: os.replace(staging,target)
        return payload|{"manifest_path":str(target/"valuation_manifest.json"),"root":str(target)}
    except Exception: shutil.rmtree(staging,ignore_errors=True); raise

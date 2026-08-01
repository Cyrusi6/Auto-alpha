"""Fail-closed governed fee evidence coverage for Task 055-C."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .evidence import canonical_hash, sha256_file

SCHEMA="task055c_fee_evidence_manifest_v1"

class FeeEvidenceError(RuntimeError): pass

def publish_fee_evidence(*,document_entries:list[dict[str,Any]],output_root:str|Path,simulation_start:str,simulation_end:str)->dict[str,Any]:
    documents=[]
    for item in document_entries:
        path=Path(item["path"])
        if not path.is_file(): raise FeeEvidenceError("fee_document_missing")
        documents.append({"component":item["component"],"publisher":item["publisher"],"url":item["url"],"sha256":sha256_file(path),"effective_start":item.get("effective_start"),"effective_end":item.get("effective_end"),"markets":item.get("markets",[]),"sides":item.get("sides",[])})
    required={(market,side,component) for market in ("SSE","SZSE") for side in ("BUY","SELL") for component in ("stamp_duty","transfer_fee","handling_fee")}
    proven=set()
    for row in documents:
        if row.get("effective_start") and row["effective_start"]<=simulation_start and (not row.get("effective_end") or row["effective_end"]>=simulation_end):
            for market in row["markets"]:
                for side in row["sides"]: proven.add((market,side,row["component"]))
    missing=sorted(required-proven)
    semantic={"schema_version":SCHEMA,"status":"passed" if not missing else "blocked","simulation_start":simulation_start,"simulation_end":simulation_end,"documents":documents,"required_rule_cells":len(required),"proven_rule_cells":len(proven),"missing_rule_cells":[{"market":x[0],"side":x[1],"component":x[2]} for x in missing],"modeled_components":["broker_commission","slippage","impact"],"certification_supported":False}
    content=canonical_hash(semantic); payload=semantic|{"content_hash":content}; root=Path(output_root); root.mkdir(parents=True,exist_ok=True); path=root/f"fee_evidence_{content[:24]}.json"; path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); return payload|{"manifest_path":str(path)}

def validate_fee_evidence(path:str|Path)->dict[str,Any]:
    payload=json.loads(Path(path).read_text()); semantic={k:v for k,v in payload.items() if k!="content_hash"}
    if payload.get("schema_version")!=SCHEMA or canonical_hash(semantic)!=payload.get("content_hash"): raise FeeEvidenceError("fee_evidence_invalid")
    return payload

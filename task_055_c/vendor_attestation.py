"""Governed vendor/document attestation for Task 055-C."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .evidence import canonical_hash, sha256_file

SCHEMA="task055c_vendor_semantic_attestation_v1"

class VendorAttestationError(RuntimeError): pass

def publish_vendor_attestation(*, documents:list[dict[str,Any]],output_root:str|Path)->dict[str,Any]:
    if len(documents)>20: raise VendorAttestationError("document_budget_exceeded")
    normalized=[]
    for item in documents:
        path=Path(item["path"])
        if not path.is_file(): raise VendorAttestationError("document_missing")
        normalized.append({"dataset":item["dataset"],"publisher":item["publisher"],"url":item["url"],"retrieved_at":item["retrieved_at"],"sha256":sha256_file(path),"size_bytes":path.stat().st_size,"semantics":{"null_timing_means_full_day":False,"empty_response_proves_trading_state":False}})
    semantic={"schema_version":SCHEMA,"status":"published","documents":sorted(normalized,key=lambda r:(r["dataset"],r["sha256"])),"suspension_timing_semantics_uncertified":True,"vendor_daily_no_trade_is_modeled_only":True}
    content=canonical_hash(semantic); payload=semantic|{"content_hash":content}; root=Path(output_root); root.mkdir(parents=True,exist_ok=True); path=root/f"vendor_attestation_{content[:24]}.json"; path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); return payload|{"manifest_path":str(path)}

"""Content-addressed exact-20 normalized replay factor store."""
from __future__ import annotations
import hashlib,json,os,tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from factor_store.models import FactorRecord
from factor_store.storage import LocalFactorStore
from .validators import canonical_hash,sha256_file

def publish_normalized_replay_store(*, source_store: str|Path, overlay: dict[str,Any], exact_ids: list[str], output_root: str|Path, semantics_contract_hash: str) -> dict[str,Any]:
    source=LocalFactorStore(source_store); source_rows={r.factor_id:r for r in source.load_factors()}; overlay_rows={r['factor_id']:r for r in overlay['records']}
    if len(exact_ids)!=20 or len(set(exact_ids))!=20: raise RuntimeError('exact20_identity_invalid')
    payloads=[]
    for fid in sorted(exact_ids):
        if fid not in source_rows or fid not in overlay_rows: raise RuntimeError(f'normalized_factor_missing:{fid}')
        original=source_rows[fid]; normalized=overlay_rows[fid]
        if original.formula_hash!=normalized['formula_hash'] or original.formula_tokens!=normalized['formula_tokens'] or original.formula!=normalized['formula']: raise RuntimeError(f'normalized_formula_identity_mismatch:{fid}')
        metadata=dict(original.metadata or {})
        metadata.update({'task054c_source_factor_record_sha256':canonical_hash(asdict(original)),'task054c_overlay_manifest_sha256':overlay['manifest_sha256'],'feature_semantics_contract_hash':semantics_contract_hash,'canonical_max_raw_lag':int(normalized['lookback_days']),'required_observations':int(normalized['canonical_required_observations']),'historical_selection_contaminated':True})
        record=FactorRecord(factor_id=original.factor_id,formula=original.formula,formula_tokens=original.formula_tokens,formula_hash=original.formula_hash,feature_version=original.feature_version,operator_version=original.operator_version,lookback_days=int(normalized['lookback_days']),created_at=original.created_at,status=str(normalized.get('policy_terminal_state') or original.status),description=original.description,metrics=original.metrics,transform_method=original.transform_method,gate_status=original.gate_status,gate_reasons=original.gate_reasons,metadata=metadata,parent_factor_ids=original.parent_factor_ids,factor_type=original.factor_type,batch_id=original.batch_id)
        payloads.append(asdict(record))
    identity_root=canonical_hash([{'factor_id':r['factor_id'],'formula_hash':r['formula_hash'],'record_hash':canonical_hash(r)} for r in payloads])
    semantic={'schema_version':'task054c_normalized_replay_store_v1','record_count':20,'identity_root':identity_root,'semantics_contract_hash':semantics_contract_hash,'overlay_content_hash':overlay['content_hash'],'overlay_manifest_sha256':overlay['manifest_sha256'],'source_store_factors_sha256':sha256_file(Path(source_store)/'factors.jsonl')}
    content_hash=canonical_hash({'semantic':semantic,'records':[canonical_hash(r) for r in payloads]}); gid=f'normalized_replay_store_{content_hash[:24]}'; root=Path(output_root); target=root/'generations'/gid
    if not target.exists():
        target.parent.mkdir(parents=True,exist_ok=True); staging=Path(tempfile.mkdtemp(prefix=f'.{gid}.',dir=target.parent)); store=LocalFactorStore(staging)
        for row in payloads: store.save_factor(FactorRecord(**row))
        records_sha=sha256_file(staging/'factors.jsonl'); manifest=semantic|{'generation_id':gid,'content_hash':content_hash,'records_file':'factors.jsonl','records_sha256':records_sha}
        (staging/'normalized_replay_store_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n'); os.replace(staging,target)
    manifest=json.loads((target/'normalized_replay_store_manifest.json').read_text()); validate_normalized_replay_store(target,expected_ids=exact_ids)
    root.mkdir(parents=True,exist_ok=True); tmp=root/'.current.tmp'; tmp.write_text(json.dumps({'generation_id':gid,'content_hash':content_hash,'manifest':f'generations/{gid}/normalized_replay_store_manifest.json'},sort_keys=True)+'\n'); os.replace(tmp,root/'current.json')
    return manifest|{'generation_dir':str(target),'manifest_path':str(target/'normalized_replay_store_manifest.json')}

def validate_normalized_replay_store(root: str|Path, *, expected_ids: list[str]) -> dict[str,Any]:
    root=Path(root); m=json.loads((root/'normalized_replay_store_manifest.json').read_text()); records=LocalFactorStore(root).load_factors()
    if sorted(r.factor_id for r in records)!=sorted(expected_ids) or len(records)!=20: raise RuntimeError('normalized_store_exact20_mismatch')
    if sha256_file(root/'factors.jsonl')!=m['records_sha256']: raise RuntimeError('normalized_store_records_sha_mismatch')
    for r in records:
        if int((r.metadata or {}).get('required_observations',-1)) != r.lookback_days+1: raise RuntimeError(f'normalized_store_lookback_unit_mismatch:{r.factor_id}')
    payloads=[asdict(record) for record in sorted(records,key=lambda row:row.factor_id)]
    identity_root=canonical_hash([{'factor_id':r['factor_id'],'formula_hash':r['formula_hash'],'record_hash':canonical_hash(r)} for r in payloads])
    if identity_root!=m.get('identity_root'): raise RuntimeError('normalized_store_identity_root_mismatch')
    semantic={key:m[key] for key in ('schema_version','record_count','identity_root','semantics_contract_hash','overlay_content_hash','overlay_manifest_sha256','source_store_factors_sha256')}
    content_hash=canonical_hash({'semantic':semantic,'records':[canonical_hash(r) for r in payloads]})
    if content_hash!=m.get('content_hash'): raise RuntimeError('normalized_store_content_hash_mismatch')
    return m|{'generation_dir':str(root),'records':records}

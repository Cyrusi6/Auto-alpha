"""Canonical engineering bundle publication."""
from __future__ import annotations
import json,os,tempfile
from pathlib import Path
from typing import Any
from .factor_store import validate_normalized_replay_store
from .validators import (
    canonical_hash,
    resolve_and_validate_overlay,
    sha256_file,
    validate_strict_matrix_generation,
    validate_v3_tensor_generation,
)

def publish_bundle(*, output_root:str|Path, freeze_manifest:str|Path, universe_manifest:str|Path, matrix:dict[str,Any], tensor:dict[str,Any], overlay:dict[str,Any], normalized_store:dict[str,Any], semantics_contract_hash:str, promotion_policy:str|Path, exact_ids:list[str]) -> dict[str,Any]:
    fm=Path(freeze_manifest); um=Path(universe_manifest); pp=Path(promotion_policy)
    semantic={'schema_version':'task054c_canonical_engineering_bundle_v1','freeze_manifest_sha256':sha256_file(fm),'freeze_content_hash':json.loads(fm.read_text()).get('content_hash'),'universe_manifest_sha256':sha256_file(um),'universe_content_hash':json.loads(um.read_text()).get('content_hash'),'matrix_content_hash':matrix['content_hash'],'matrix_manifest_sha256':matrix['manifest_sha256'],'tensor_content_hash':tensor['content_hash'],'tensor_manifest_sha256':tensor['manifest_sha256'],'semantics_contract_hash':semantics_contract_hash,'overlay_content_hash':overlay['content_hash'],'overlay_manifest_sha256':overlay['manifest_sha256'],'eligible_date_hash':matrix['eligible_date_hash'],'stock_axis_hash':matrix['stock_axis_hash'],'date_axis_hash':matrix['date_axis_hash'],'feature_axis_hash':tensor['feature_axis_hash'],'target_contract':matrix['manifest']['target_contract'],'time_contract':matrix['manifest']['firewall'],'promotion_policy_sha256':sha256_file(pp),'normalized_store_content_hash':normalized_store['content_hash'],'normalized_store_manifest_sha256':sha256_file(normalized_store['manifest_path']),'exact20_identity_root':normalized_store['identity_root'],'exact20_ids':sorted(exact_ids),'historical_selection_contaminated':True}
    content_hash=canonical_hash(semantic); gid=f'engineering_bundle_{content_hash[:24]}'; root=Path(output_root); target=root/'generations'/gid
    payload=semantic|{'generation_id':gid,'content_hash':content_hash,'artifact_paths':{'freeze_manifest':str(fm),'universe_manifest':str(um),'matrix_root':matrix['root'],'tensor_root':tensor['root'],'overlay_root':overlay['root'],'normalized_store_root':normalized_store['generation_dir'],'promotion_policy':str(pp)}}
    if not target.exists(): target.mkdir(parents=True); (target/'engineering_bundle_manifest.json').write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')
    validate_bundle(target/'engineering_bundle_manifest.json')
    root.mkdir(parents=True,exist_ok=True); tmp=root/'.current.tmp'; tmp.write_text(json.dumps({'generation_id':gid,'content_hash':content_hash,'manifest':f'generations/{gid}/engineering_bundle_manifest.json'},sort_keys=True)+'\n'); os.replace(tmp,root/'current.json')
    return payload|{'manifest_path':str(target/'engineering_bundle_manifest.json'),'generation_dir':str(target)}

def validate_bundle(path:str|Path)->dict[str,Any]:
    p=Path(path); m=json.loads(p.read_text()); semantic={k:v for k,v in m.items() if k not in {'generation_id','content_hash','artifact_paths'}}
    if canonical_hash(semantic)!=m.get('content_hash'): raise RuntimeError('bundle_content_hash_mismatch')
    if len(m.get('exact20_ids') or [])!=20: raise RuntimeError('bundle_exact20_mismatch')
    paths=m.get('artifact_paths') or {}
    required={'freeze_manifest','universe_manifest','matrix_root','tensor_root','overlay_root','normalized_store_root','promotion_policy'}
    if set(paths)!=required: raise RuntimeError('bundle_artifact_paths_invalid')
    freeze=Path(paths['freeze_manifest']); universe=Path(paths['universe_manifest']); promotion=Path(paths['promotion_policy'])
    if sha256_file(freeze)!=m['freeze_manifest_sha256'] or json.loads(freeze.read_text()).get('content_hash')!=m['freeze_content_hash']: raise RuntimeError('bundle_freeze_lineage_mismatch')
    if sha256_file(universe)!=m['universe_manifest_sha256'] or json.loads(universe.read_text()).get('content_hash')!=m['universe_content_hash']: raise RuntimeError('bundle_universe_lineage_mismatch')
    if sha256_file(promotion)!=m['promotion_policy_sha256']: raise RuntimeError('bundle_promotion_policy_mismatch')
    matrix=validate_strict_matrix_generation(paths['matrix_root'],expected_content_hash=m['matrix_content_hash'])
    tensor=validate_v3_tensor_generation(paths['tensor_root'],matrix=matrix,expected_content_hash=m['tensor_content_hash'])
    overlay=resolve_and_validate_overlay(paths['overlay_root'],expected_content_hash=m['overlay_content_hash'])
    store=validate_normalized_replay_store(paths['normalized_store_root'],expected_ids=m['exact20_ids'])
    store_manifest=Path(paths['normalized_store_root'])/'normalized_replay_store_manifest.json'
    checks=(matrix['manifest_sha256']==m['matrix_manifest_sha256'],tensor['manifest_sha256']==m['tensor_manifest_sha256'],overlay['manifest_sha256']==m['overlay_manifest_sha256'],sha256_file(store_manifest)==m['normalized_store_manifest_sha256'],store['content_hash']==m['normalized_store_content_hash'],store['identity_root']==m['exact20_identity_root'])
    if not all(checks): raise RuntimeError('bundle_native_artifact_lineage_mismatch')
    return m

"""Five-stage pre-GPU gate seal."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any
from .bundle import validate_bundle
from .run import validate_sentinel
from .validators import canonical_hash,sha256_file

_VALIDATED_SEAL_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}

def code_semantic_hash()->str:
 root=Path(__file__).resolve().parents[1];paths=['task_054_c/contracts.py','task_054_c/validators.py','task_054_c/bundle.py','task_054_c/factor_store.py','task_054_c/research_view.py','task_054_c/audit.py','task_054_c/run.py','task_054_c/worker.py','task_054_c/seal.py','task_054_c/final_verifier.py','validation_lab/materialization.py','validation_lab/run_validation.py','validation_lab/policy.py','validation_campaign_store/scheduler.py','validation_campaign_store/replay_evidence.py','model_core/vm.py'];h=hashlib.sha256()
 for name in paths:h.update(name.encode());h.update((root/name).read_bytes())
 return h.hexdigest()
def publish_pre_gpu_seal(*,bundle_manifest:str|Path,mutation_manifest:str|Path,sentinel_manifest:str|Path,validation_policy_hash:str,output_path:str|Path)->dict[str,Any]:
 bundle=validate_bundle(bundle_manifest);sentinel=validate_sentinel(sentinel_manifest,root=Path(sentinel_manifest).parent)
 execution_roots={f'{mutation}:{path_name}':{'projection_manifest_sha256':row['projection_manifest_sha256'],'receipt_root':row['receipt_root'],'ledger_root':row['ledger_root'],'research_semantic_hash':row['semantic']['research_semantic_hash']} for mutation,paths in sentinel['executions'].items() for path_name,row in paths.items()}
 projection_lineage=_projection_lineage(Path(sentinel_manifest).parent,sentinel)
 stages={'bundle':{'content_hash':bundle['content_hash'],'manifest_sha256':sha256_file(bundle_manifest)},'identity':{'normalized_store_content_hash':bundle['normalized_store_content_hash'],'exact20_identity_root':bundle['exact20_identity_root'],'overlay_content_hash':bundle['overlay_content_hash']},'research':{'eligible_date_hash':bundle['eligible_date_hash'],'matrix_content_hash':bundle['matrix_content_hash'],'tensor_content_hash':bundle['tensor_content_hash'],'baseline_projection_matrix_content_hash':projection_lineage['matrix_content_hash'],'baseline_projection_tensor_content_hash':projection_lineage['tensor_content_hash'],'baseline_projection_content_hash':projection_lineage['projection_content_hash'],'semantics_contract_hash':bundle['semantics_contract_hash'],'mutation_manifest_sha256':sha256_file(mutation_manifest),'execution_roots':execution_roots,'execution_root_hash':canonical_hash(execution_roots)},'sentinel':{'content_hash':sentinel['content_hash'],'manifest_sha256':sha256_file(sentinel_manifest)},'policy_code':{'validation_policy_hash':validation_policy_hash,'code_semantic_hash':code_semantic_hash()}}
 payload={'schema_version':'task054c_pre_gpu_gate_seal_v1','status':'sealed','stages':stages,'bundle_hash':bundle['content_hash'],'eligible_date_hash':bundle['eligible_date_hash'],'exact20_identity_root':bundle['exact20_identity_root'],'source_manifests':{'bundle':str(Path(bundle_manifest).resolve()),'sentinel':str(Path(sentinel_manifest).resolve())},'certification_ready':False,'portfolio_ready':False,'paper_ready':False,'live_ready':False};payload['seal_hash']=canonical_hash(payload);p=Path(output_path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n');return payload|{'seal_path':str(p)}
def validate_pre_gpu_seal(path:str|Path,*,bundle_manifest:str|Path|None=None)->dict[str,Any]:
 p=Path(path);s=json.loads(p.read_text());semantic={k:v for k,v in s.items() if k!='seal_hash'}
 if s.get('status')!='sealed' or canonical_hash(semantic)!=s.get('seal_hash'):raise RuntimeError('pre_gpu_seal_invalid')
 resolved_bundle=Path(bundle_manifest or (s.get('source_manifests') or {}).get('bundle',''))
 cache_key=(sha256_file(p),sha256_file(resolved_bundle) if resolved_bundle.is_file() else '',code_semantic_hash())
 if cache_key in _VALIDATED_SEAL_CACHE:return dict(_VALIDATED_SEAL_CACHE[cache_key])
 sentinel_path=Path((s.get('source_manifests') or {}).get('sentinel',''))
 if not resolved_bundle.is_file() or validate_bundle(resolved_bundle)['content_hash']!=s['bundle_hash']:raise RuntimeError('pre_gpu_seal_bundle_mismatch')
 if not sentinel_path.is_file():raise RuntimeError('pre_gpu_seal_sentinel_missing')
 sentinel=validate_sentinel(sentinel_path,root=sentinel_path.parent)
 if sentinel.get('content_hash')!=s['stages']['sentinel']['content_hash'] or sha256_file(sentinel_path)!=s['stages']['sentinel']['manifest_sha256']:raise RuntimeError('pre_gpu_seal_sentinel_mismatch')
 if s['stages']['policy_code'].get('code_semantic_hash')!=code_semantic_hash():raise RuntimeError('pre_gpu_seal_code_semantic_mismatch')
 if any(s.get(k) is not False for k in ('certification_ready','portfolio_ready','paper_ready','live_ready')):raise RuntimeError('pre_gpu_seal_downstream_readiness_invalid')
 _VALIDATED_SEAL_CACHE[cache_key]=dict(s)
 return s

def _projection_lineage(root:Path,sentinel:dict[str,Any])->dict[str,str]:
 expected=sentinel['executions']['baseline']['matrix_local']['projection_manifest_sha256'];matches=[]
 for path in (root/'runs'/'matrix_local'/'baseline').glob('research_projection*/generations/*/research_projection_manifest.json'):
  if sha256_file(path)==expected: matches.append(path)
 if len(matches)!=1: raise RuntimeError('pre_gpu_baseline_projection_manifest_unresolved')
 manifest=json.loads(matches[0].read_text());return {'projection_content_hash':manifest['content_hash'],'matrix_content_hash':manifest['matrix_content_hash'],'tensor_content_hash':manifest['tensor_content_hash']}

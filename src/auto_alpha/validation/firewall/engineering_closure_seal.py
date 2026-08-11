"""Five-stage pre-GPU gate seal."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any
from auto_alpha._paths import semantic_source_hash
from auto_alpha.validation.firewall.engineering_closure_bundle import validate_bundle
from auto_alpha.validation.firewall.engineering_closure_contracts import EVIDENCE_SCOPE, RUN_MUTATIONS, RUN_PATHS
from auto_alpha.validation.firewall.engineering_closure_research_view import load_research_projection_manifest, validate_research_projection
from auto_alpha.validation.firewall.engineering_closure_validators import canonical_hash
from auto_alpha.validation.firewall.engineering_closure_validators import sha256_file

_VALIDATED_SEAL_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}

def validate_sentinel(path:str|Path,*,root:str|Path)->dict[str,Any]:
 p=Path(path);artifact=json.loads(p.read_text())
 executions=artifact.get('executions') or {}
 if artifact.get('evidence_scope')!=EVIDENCE_SCOPE or artifact.get('status')!='passed':raise RuntimeError('sentinel_status_scope_invalid')
 if set(executions)!=set(RUN_MUTATIONS) or any(set(executions[mutation])!=set(RUN_PATHS) for mutation in RUN_MUTATIONS):raise RuntimeError('sentinel_exact12_invalid')
 if canonical_hash({key:value for key,value in artifact.items() if key!='content_hash'})!=artifact.get('content_hash'):raise RuntimeError('sentinel_content_hash_mismatch')
 base=Path(root)
 for mutation in RUN_MUTATIONS:
  projection_proofs=set()
  for path_name in RUN_PATHS:
   execution=executions[mutation][path_name];output=base/'runs'/path_name/mutation
   native=json.loads((output/'auto_alpha.execution.trading.engine.json').read_text())
   if native!=execution or execution.get('status')!='success':raise RuntimeError('sentinel_execution_invalid')
   if canonical_hash({key:value for key,value in execution.items() if key!='content_hash'})!=execution.get('content_hash'):raise RuntimeError('sentinel_execution_hash_invalid')
   receipts=_jsonl(output/'component_receipts.jsonl');previous='0'*64
   if not receipts:raise RuntimeError('sentinel_receipts_missing')
   for row in receipts:
    if row.get('status')!='success' or not row.get('output_artifacts') or row.get('parent_receipt_hash')!=previous:raise RuntimeError('sentinel_receipt_chain_invalid')
    if canonical_hash({key:value for key,value in row.items() if key!='receipt_hash'})!=row.get('receipt_hash'):raise RuntimeError('sentinel_receipt_hash_invalid')
    previous=row['receipt_hash']
   ledger=_jsonl(output/'read_ledger.jsonl');previous='0'*64
   if not ledger:raise RuntimeError('sentinel_read_ledger_missing')
   for row in ledger:
    if row.get('policy_decision')!='allow' or row.get('principal')!='research' or row.get('date_range',[None,'99999999'])[1]>'20240528':raise RuntimeError('sentinel_read_ledger_invalid')
    if row.get('previous_entry_hash')!=previous or canonical_hash({key:value for key,value in row.items() if key!='entry_hash'})!=row.get('entry_hash'):raise RuntimeError('sentinel_read_ledger_chain_invalid')
    previous=row['entry_hash']
   projection_path=_find_projection_manifest(output,execution['projection_manifest_sha256']);projection=load_research_projection_manifest(projection_path)
   projection_proofs.add((projection['research_computation_identity'],projection['matrix_content_hash'],projection['tensor_content_hash']))
  if len(projection_proofs)!=1:raise RuntimeError(f'sentinel_projection_semantic_mismatch:{mutation}')
  validate_research_projection(_find_projection_manifest(base/'runs'/'matrix_local'/mutation,executions[mutation]['matrix_local']['projection_manifest_sha256']))
 _validate_sentinel_invariants(executions)
 return artifact

def _find_projection_manifest(output:Path,expected_sha256:str)->Path:
 matches=[candidate for candidate in output.glob('research_projection*/generations/*/research_projection_manifest.json') if sha256_file(candidate)==expected_sha256]
 if len(matches)!=1:raise RuntimeError(f'sentinel_projection_manifest_unresolved:{output}')
 return matches[0]

def _validate_sentinel_invariants(executions:dict[str,Any])->None:
 baseline=executions['baseline'];post=executions['post_cutoff'];inside=executions['inside_cutoff']
 if len({baseline[path]['semantic']['research_semantic_hash'] for path in RUN_PATHS})!=1:raise RuntimeError('sentinel_baseline_semantic_mismatch')
 for path in RUN_PATHS:
  if post[path]['semantic']['research_semantic_hash']!=baseline[path]['semantic']['research_semantic_hash']:raise RuntimeError(f'sentinel_post_cutoff_changed:{path}')
  if post[path]['semantic']['diagnostic_hash']==baseline[path]['semantic']['diagnostic_hash']:raise RuntimeError(f'sentinel_diagnostic_unchanged:{path}')
  if inside[path]['semantic']['research_semantic_hash']==baseline[path]['semantic']['research_semantic_hash']:raise RuntimeError(f'sentinel_inside_cutoff_unchanged:{path}')

def _jsonl(path:Path)->list[dict[str,Any]]:
 return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []

def code_semantic_hash()->str:
 return semantic_source_hash(('auto_alpha.validation.firewall.engineering_closure_contracts','auto_alpha.validation.firewall.engineering_closure_validators','auto_alpha.validation.firewall.engineering_closure_bundle','auto_alpha.validation.firewall.engineering_closure_factor_store','auto_alpha.validation.firewall.engineering_closure_research_view','auto_alpha.validation.firewall.engineering_closure_seal','auto_alpha.validation.firewall.production_sentinel_sentinel','auto_alpha.validation.walk_forward.engine_materialization','auto_alpha.validation.walk_forward.engine_run_validation','auto_alpha.validation.walk_forward.engine_policy','auto_alpha.validation.walk_forward.campaigns_scheduler','auto_alpha.validation.walk_forward.campaigns_replay_evidence','auto_alpha.research.formulas.vm'))
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

"""Unique Task 054-C production runner and sentinel verifier."""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
from typing import Any
from compute_cluster import LocalComputeScheduler
from compute_cluster.models import ComputeDeviceType,ComputeJobKind,ComputeJobSpec,ComputeSchedulerConfig
from .audit import ALLOWED_COMPONENTS,source_hash
from .bundle import validate_bundle
from .contracts import EVIDENCE_SCOPE,PROTOCOL_VERSION,RUN_MUTATIONS,RUN_PATHS,SENTINEL_SCHEMA
from .research_view import load_research_projection_manifest,publish_research_projection,validate_research_projection
from .validators import canonical_hash,sha256_file,validate_strict_matrix_generation,validate_v3_tensor_generation

def run(config_path:str|Path)->dict[str,Any]:
    config=json.loads(Path(config_path).read_text()); bundle=validate_bundle(config['bundle_manifest']); root=Path(config['output_root']);root.mkdir(parents=True,exist_ok=True)
    mutations=json.loads(Path(config['mutation_manifest']).read_text()); generations=mutations['generations']; projections={}
    for mutation in RUN_MUTATIONS:
        g=generations[mutation]; matrix=validate_strict_matrix_generation(g['matrix_dir']); tensor=validate_v3_tensor_generation(g['tensor_dir'],matrix=matrix)
        projection=publish_research_projection(matrix_root=matrix['root'],tensor_root=tensor['root'],output_root=root/'projections_a'/mutation); projection=validate_research_projection(projection['manifest_path']);projections[mutation]=projection
        g.update({'matrix_content_hash':matrix['content_hash'],'tensor_content_hash':tensor['content_hash'],'projection_manifest':projection['manifest_path']})
    normalized_root=bundle['artifact_paths']['normalized_store_root']; probe=config.get('probe_factor_id','factor_369358b247706fb5'); specs=[]
    for path_name in RUN_PATHS:
        source_kind,execution_kind=path_name.split('_',1); cache_namespace=root/'cache_namespaces'/path_name
        for mutation in RUN_MUTATIONS:
            g=generations[mutation]; invocation=f"task054c_{path_name}_{mutation}_{bundle['content_hash'][:10]}"; out=root/'runs'/path_name/mutation
            worker={'schema_version':'task054c_worker_config_v1','evidence_scope':EVIDENCE_SCOPE,'protocol_version':PROTOCOL_VERSION,'invocation_id':invocation,'path_name':path_name,'source_kind':source_kind,'execution_kind':execution_kind,'mutation':mutation,'output_dir':str(out),'cache_namespace':str(cache_namespace),'freeze_dir':g['freeze_dir'],'freeze_manifest':str(next(Path(g['freeze_dir']).glob('*freeze*manifest*.json'))),'universe_dir':bundle['artifact_paths']['universe_manifest'].rsplit('/',1)[0],'universe_manifest':bundle['artifact_paths']['universe_manifest'],'matrix_dir':g['matrix_dir'],'matrix_manifest':str(Path(g['matrix_dir'])/'task_052a_strict_matrix_manifest.json'),'tensor_dir':g['tensor_dir'],'tensor_manifest':str(Path(g['tensor_dir'])/'task_053_v3_tensor_manifest.json'),'projection_a_manifest':projections[mutation]['manifest_path'],'expected_generation':{'matrix_content_hash':g['matrix_content_hash'],'tensor_content_hash':g['tensor_content_hash']},'feature_manifest':config['feature_manifest'],'promotion_policy':bundle['artifact_paths']['promotion_policy'],'factor_store_dir':normalized_root,'probe_factor_id':probe,'eligible_date_hash':bundle['eligible_date_hash']}
            cfg=root/'worker_configs'/f'{path_name}_{mutation}.json';cfg.parent.mkdir(parents=True,exist_ok=True);cfg.write_text(json.dumps(worker,indent=2,sort_keys=True)+'\n'); specs.append((path_name,mutation,execution_kind,cfg,out))
    scheduler_evidence={}
    for path_name in RUN_PATHS:
        for mutation in RUN_MUTATIONS:
            _,_,execution,cfg,out=next(x for x in specs if x[0]==path_name and x[1]==mutation)
            if execution=='local':
                log=root/'logs'/f'{path_name}_{mutation}.log';log.parent.mkdir(parents=True,exist_ok=True)
                with log.open('w') as h:
                    p=subprocess.run([sys.executable,'-m','task_054_c.worker','--config',str(cfg)],cwd=str(Path(__file__).resolve().parents[1]),stdout=h,stderr=subprocess.STDOUT)
                if p.returncode: raise RuntimeError(f'local_worker_failed:{path_name}:{mutation}:{log}')
            else:
                job_id=f"sentinel_{canonical_hash({'path':path_name,'mutation':mutation,'bundle':bundle['content_hash']})[:20]}";state=root/'scheduler_state'/path_name
                scheduler=LocalComputeScheduler(ComputeSchedulerConfig(state_dir=str(state),output_dir=str(root/'scheduler_output'/path_name),max_parallel_cpu_jobs=1,max_parallel_gpu_jobs=0,fail_fast=True,resume=False))
                job=ComputeJobSpec(job_id=job_id,job_kind=ComputeJobKind.SHELL_COMMAND,command=[sys.executable,'-m','task_054_c.worker','--config',str(cfg)],cwd=str(Path(__file__).resolve().parents[1]),input_paths=[str(cfg),config['bundle_manifest']],output_dir=str(out),required_device_type=ComputeDeviceType.CPU,max_duration_seconds=float(config.get('timeout_seconds',3600)),max_retries=0,metadata={'task':'054-C','path':path_name,'mutation':mutation,'bundle_hash':bundle['content_hash']})
                submitted=scheduler.submit_jobs([job]);
                if submitted['submitted']!=1: raise RuntimeError(f'scheduler_job_not_submitted:{job_id}')
                scheduler.run(); runs=[r for r in scheduler.store.read_runs() if r.get('job_id')==job_id]; hearts=[r for r in _jsonl(scheduler.store.heartbeats_path) if r.get('job_id')==job_id]
                if len(runs)!=1 or runs[0].get('status')!='success' or runs[0].get('return_code')!=0: raise RuntimeError(f'scheduler_job_failed:{job_id}')
                scheduler_evidence[f'{path_name}:{mutation}']={'job_id':job_id,'run_id':runs[0].get('run_id'),'attempt':runs[0].get('attempt'),'status':runs[0].get('status'),'return_code':runs[0].get('return_code'),'heartbeat_count':len(hearts),'command_hash':canonical_hash(job.command),'config_sha256':sha256_file(cfg)}
    executions={}
    for path_name,mutation,_,_,out in specs:
        payload=json.loads((out/'execution.json').read_text()); executions.setdefault(mutation,{})[path_name]=payload
    artifact={'schema_version':SENTINEL_SCHEMA,'status':'passed','evidence_scope':EVIDENCE_SCOPE,'protocol_version':PROTOCOL_VERSION,'bundle_hash':bundle['content_hash'],'mutation_manifest_sha256':sha256_file(config['mutation_manifest']),'executions':executions,'scheduler_evidence':scheduler_evidence,'threat_model':'supervisor_attested_tamper_evident_not_cryptographically_unforgeable'}
    artifact['proof']=_invariants(executions); artifact['content_hash']=canonical_hash({k:v for k,v in artifact.items() if k!='content_hash'}); path=root/'task054c_production_sentinel.json';path.write_text(json.dumps(artifact,indent=2,sort_keys=True)+'\n'); validate_sentinel(path,root=root);return artifact|{'manifest_path':str(path)}
def validate_sentinel(path:str|Path,*,root:str|Path)->dict[str,Any]:
    p=Path(path);a=json.loads(p.read_text());
    if a.get('evidence_scope')!=EVIDENCE_SCOPE or a.get('status')!='passed': raise RuntimeError('sentinel_status_scope_invalid')
    if set(a.get('executions') or {})!=set(RUN_MUTATIONS) or any(set(a['executions'][m])!=set(RUN_PATHS) for m in RUN_MUTATIONS): raise RuntimeError('sentinel_exact12_invalid')
    if canonical_hash({k:v for k,v in a.items() if k!='content_hash'})!=a.get('content_hash'): raise RuntimeError('sentinel_content_hash_mismatch')
    for mutation in RUN_MUTATIONS:
      projection_semantics=set()
      for path_name in RUN_PATHS:
        execution=a['executions'][mutation][path_name];out=Path(root)/'runs'/path_name/mutation
        native_execution=json.loads((out/'execution.json').read_text())
        if execution.get('status')!='success' or native_execution!=execution: raise RuntimeError('execution_invalid')
        if canonical_hash({k:v for k,v in execution.items() if k!='content_hash'})!=execution.get('content_hash'): raise RuntimeError('execution_content_hash_invalid')
        receipts=_jsonl(out/'component_receipts.jsonl'); expected_components=set(ALLOWED_COMPONENTS)
        observed={r['component'] for r in receipts}
        required=expected_components-({'strict_matrix_builder','v3_tensor_builder'} if path_name.startswith('matrix_') else {'strict_matrix_validator','v3_tensor_validator'})
        if observed!=required: raise RuntimeError(f'component_set_invalid:{path_name}:{sorted(observed)}')
        previous='0'*64
        for row in receipts:
            if row['entrypoint']!=ALLOWED_COMPONENTS[row['component']]: raise RuntimeError('receipt_fqn_invalid')
            obj=_resolve_fqn(row['entrypoint'])
            if row.get('status')!='success' or not row.get('output_artifacts'): raise RuntimeError('receipt_semantic_failure')
            if any(not item.get('sha256') for item in (row.get('input_artifacts') or {}).values()) or any(not item.get('sha256') for item in (row.get('output_artifacts') or {}).values()): raise RuntimeError('receipt_artifact_hash_missing')
            if row['source_hash']!=source_hash(obj) or row['parent_receipt_hash']!=previous or canonical_hash({k:v for k,v in row.items() if k!='receipt_hash'})!=row['receipt_hash']: raise RuntimeError('receipt_chain_invalid')
            previous=row['receipt_hash']
        ledger=_jsonl(out/'read_ledger.jsonl'); previous='0'*64
        if not ledger: raise RuntimeError('read_ledger_missing')
        for row in ledger:
            if row['policy_decision']!='allow' or row['principal']!='research' or row['date_range'][1]>'20240528': raise RuntimeError('read_ledger_invalid')
            relative=row.get('relative_path')
            projection_path=_find_projection_manifest(out,execution['projection_manifest_sha256'])
            target=(projection_path.parent/relative).resolve() if relative else None
            if target is None or not target.is_file() or sha256_file(target)!=row.get('sha256'): raise RuntimeError('read_ledger_artifact_mismatch')
            if row['previous_entry_hash']!=previous or canonical_hash({k:v for k,v in row.items() if k!='entry_hash'})!=row['entry_hash']: raise RuntimeError('read_ledger_chain_invalid')
            previous=row['entry_hash']
        if path_name.endswith('_scheduler'):
            _validate_scheduler_evidence(a,Path(root),path_name,mutation)
        projection_path=_find_projection_manifest(out,execution['projection_manifest_sha256']); projection=load_research_projection_manifest(projection_path);projection_semantics.add(_projection_semantic_proof(projection_path,projection))
      if len(projection_semantics)!=1: raise RuntimeError(f'projection_semantic_mismatch:{mutation}')
      validate_research_projection(_find_projection_manifest(Path(root)/'runs'/'matrix_local'/mutation,a['executions'][mutation]['matrix_local']['projection_manifest_sha256']))
    _invariants(a['executions']);return a

def _validate_scheduler_evidence(artifact:dict[str,Any],root:Path,path_name:str,mutation:str)->None:
    evidence=(artifact.get('scheduler_evidence') or {}).get(f'{path_name}:{mutation}')
    if not evidence: raise RuntimeError(f'scheduler_evidence_missing:{path_name}:{mutation}')
    state=root/'scheduler_state'/path_name
    jobs=_jsonl(state/'compute_jobs.jsonl'); runs=_jsonl(state/'compute_job_runs.jsonl'); hearts=_jsonl(state/'compute_heartbeats.jsonl')
    matching_jobs=[row for row in jobs if row.get('job_id')==evidence.get('job_id')]
    if len(matching_jobs)!=1 or canonical_hash(matching_jobs[0].get('command') or [])!=evidence.get('command_hash'): raise RuntimeError(f'scheduler_job_spec_mismatch:{path_name}:{mutation}')
    matching=[row for row in runs if row.get('job_id')==evidence.get('job_id')]
    if len(matching)!=1: raise RuntimeError(f'scheduler_run_cardinality:{path_name}:{mutation}')
    run=matching[0]
    if run.get('run_id')!=evidence.get('run_id') or run.get('attempt')!=evidence.get('attempt') or run.get('status')!='success' or run.get('return_code')!=0: raise RuntimeError(f'scheduler_state_mismatch:{path_name}:{mutation}')
    heartbeat_count=sum(1 for row in hearts if row.get('job_id')==evidence.get('job_id'))
    if heartbeat_count!=evidence.get('heartbeat_count') or heartbeat_count<1: raise RuntimeError(f'scheduler_heartbeat_mismatch:{path_name}:{mutation}')
    config_path=root/'worker_configs'/f'{path_name}_{mutation}.json'
    if sha256_file(config_path)!=evidence.get('config_sha256'): raise RuntimeError(f'scheduler_config_mismatch:{path_name}:{mutation}')

def _find_projection_manifest(out:Path,expected_sha256:str)->Path:
    matches=[path for path in out.glob('research_projection*/generations/*/research_projection_manifest.json') if sha256_file(path)==expected_sha256]
    if len(matches)!=1: raise RuntimeError(f'projection_manifest_unresolved:{out}')
    return matches[0]

def _projection_semantic_proof(path:Path,projection:dict[str,Any])->tuple[str,...]:
    root=path.parent;matrix=json.loads((root/projection['matrix_root']/'task_052a_strict_matrix_manifest.json').read_text());tensor=json.loads((root/projection['tensor_root']/'task_053_v3_tensor_manifest.json').read_text())
    return (projection['research_computation_identity'],canonical_hash(matrix['partition_sha256']),matrix['date_axis_hash'],tensor['values_sha256'],tensor['validity_sha256'],tensor['feature_axis_hash'])
def _invariants(e):
    baseline=e['baseline'];post=e['post_cutoff'];inside=e['inside_cutoff']; base_hash={p:baseline[p]['semantic']['research_semantic_hash'] for p in RUN_PATHS}
    if len(set(base_hash.values()))!=1: raise RuntimeError('baseline_path_semantic_mismatch')
    for p in RUN_PATHS:
        if post[p]['semantic']['research_semantic_hash']!=baseline[p]['semantic']['research_semantic_hash']: raise RuntimeError(f'post_cutoff_research_changed:{p}')
        if post[p]['semantic']['diagnostic_hash']==baseline[p]['semantic']['diagnostic_hash']: raise RuntimeError(f'post_cutoff_diagnostic_unchanged:{p}')
        if not post[p]['semantic']['formula_batch_cache_hit'] or not post[p]['semantic']['materialization_cache_hit']: raise RuntimeError(f'post_cutoff_cache_miss:{p}')
        if inside[p]['semantic']['research_semantic_hash']==baseline[p]['semantic']['research_semantic_hash']: raise RuntimeError(f'inside_cutoff_research_unchanged:{p}')
        if inside[p]['semantic']['formula_batch_cache_hit'] or inside[p]['semantic']['materialization_cache_hit']: raise RuntimeError(f'inside_cutoff_cache_hit:{p}')
    return {'exact_12':True,'baseline_consistent':True,'post_cutoff_invariant':True,'post_cutoff_cache_hit':True,'diagnostic_changed':True,'inside_cutoff_changed':True,'inside_cutoff_cache_miss':True}
def _jsonl(path):
 p=Path(path);return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
def _resolve_fqn(value):
 parts=value.split('.')
 for index in range(len(parts),0,-1):
  try: obj=__import__('.'.join(parts[:index]),fromlist=['*'])
  except ModuleNotFoundError: continue
  for part in parts[index:]: obj=getattr(obj,part)
  return obj
 raise RuntimeError(f'component_fqn_unresolvable:{value}')
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args(argv);print(json.dumps(run(a.config),sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())

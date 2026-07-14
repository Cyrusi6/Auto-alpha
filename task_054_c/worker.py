"""One native Task 054-C production sentinel execution."""
from __future__ import annotations
import argparse,hashlib,json,time
from pathlib import Path
from typing import Any
import numpy as np,torch
from alpha_factory.models import AlphaCandidateRecord
from alpha_factory.proxy_eval import run_proxy_eval
from artifact_schema.writer import write_jsonl_artifact
from factor_store.storage import LocalFactorStore
from feature_factory.builder import load_feature_manifest
from feature_factory.vocab_adapter import make_formula_vocab_from_manifest
from formula_batch_eval import FormulaBatchEvalConfig,FormulaBatchEvaluator
from formula_batch_eval.models import FormulaEvalRequest
from matrix_store import StrictEngineeringPITMatrixBuilder,StrictEngineeringPITMatrixConfig
from model_core.data_loader import AShareDataLoader
from model_core.vm import StackVM
from task_053_a.orchestrator import build_v3_tensor_generation
from validation_campaign_store.consolidate import consolidate_validation_results
from validation_campaign_store.ingest import ingest_candidate_pool
from validation_campaign_store.registry import LocalValidationCampaignStore
from validation_campaign_store.scheduler import plan_validation_shards
from validation_lab.materialization import FactorMaterializer,MaterializationInputs
from validation_lab.policy import load_validation_policy
from validation_lab.run_validation import run_validation_service
from .audit import ReceiptRecorder,SupervisorLedger
from .research_view import load_research_projection_manifest,publish_research_projection
from .validators import canonical_hash,sha256_file,validate_strict_matrix_generation,validate_v3_tensor_generation

def run_worker(config_path:str|Path)->dict[str,Any]:
    config=json.loads(Path(config_path).read_text()); output=Path(config['output_dir']);output.mkdir(parents=True,exist_ok=True)
    source_kind=config['source_kind']; mutation=config['mutation']; expected=config['expected_generation']; projection_a=load_research_projection_manifest(config['projection_a_manifest'])
    receipts=ReceiptRecorder(output/'component_receipts.jsonl',invocation_id=config['invocation_id'])
    if source_kind=='raw':
        builder=StrictEngineeringPITMatrixBuilder(StrictEngineeringPITMatrixConfig(research_observable_cutoff='20240530',target_endpoint_horizon_trade_days=2,min_cross_section_breadth=30))
        matrix_result=receipts.invoke('strict_matrix_builder',builder.build,governed_freeze_dir=config['freeze_dir'],historical_universe_dir=config['universe_dir'],output_root=output/'raw_matrix_b',input_artifacts={'freeze_manifest':config['freeze_manifest'],'universe_manifest':config['universe_manifest']},output_artifacts=lambda r:{'matrix_manifest':r.manifest_path})
        matrix=validate_strict_matrix_generation(matrix_result.generation_dir)
        tensor_result=receipts.invoke('v3_tensor_builder',build_v3_tensor_generation,matrix_dir=matrix['root'],feature_manifest_path=config['feature_manifest'],output_root=output/'raw_tensor_b',candidate_pool_path=None,input_artifacts={'matrix_manifest':matrix['manifest_path'],'feature_manifest':config['feature_manifest']},output_artifacts=lambda r:{'tensor_manifest':Path(r['generation_dir'])/'task_053_v3_tensor_manifest.json'})
        tensor=validate_v3_tensor_generation(tensor_result['generation_dir'],matrix=matrix)
        if matrix['content_hash']!=expected['matrix_content_hash'] or tensor['content_hash']!=expected['tensor_content_hash']: raise RuntimeError('raw_a_b_generation_mismatch')
        projection=receipts.invoke('research_projection_publisher',publish_research_projection,matrix_root=matrix['root'],tensor_root=tensor['root'],output_root=output/'research_projection_b',input_artifacts={'matrix_manifest':matrix['manifest_path'],'tensor_manifest':tensor['manifest_path']},output_artifacts=lambda r:{'projection_manifest':r['manifest_path']})
        projection=load_research_projection_manifest(projection['manifest_path'])
        if projection['research_computation_identity']!=projection_a['research_computation_identity']: raise RuntimeError('raw_a_b_research_projection_mismatch')
    else:
        matrix=receipts.invoke('strict_matrix_validator',validate_strict_matrix_generation,config['matrix_dir'],input_artifacts={'matrix_manifest':config['matrix_manifest']},output_artifacts=lambda r:{'matrix_manifest':r['manifest_path']},semantic_success=lambda r:r['content_hash']==expected['matrix_content_hash'])
        tensor=receipts.invoke('v3_tensor_validator',validate_v3_tensor_generation,config['tensor_dir'],matrix=matrix,input_artifacts={'tensor_manifest':config['tensor_manifest'],'matrix_manifest':config['matrix_manifest']},output_artifacts=lambda r:{'tensor_manifest':r['manifest_path']},semantic_success=lambda r:r['content_hash']==expected['tensor_content_hash'])
        projection=receipts.invoke('research_projection_publisher',publish_research_projection,matrix_root=matrix['root'],tensor_root=tensor['root'],output_root=output/'research_projection_verified',input_artifacts={'matrix_manifest':matrix['manifest_path'],'tensor_manifest':tensor['manifest_path']},output_artifacts=lambda r:{'projection_manifest':r['manifest_path']})
        projection=load_research_projection_manifest(projection['manifest_path'])
        if projection['research_computation_identity']!=projection_a['research_computation_identity']: raise RuntimeError('matrix_projection_identity_mismatch')
    ledger=SupervisorLedger(output/'read_ledger.jsonl',invocation_id=config['invocation_id'],projection_manifest=projection['manifest_path'])
    matrix_dir=Path(projection['matrix_dir']);tensor_dir=Path(projection['tensor_dir'])
    for component,dataset,path in [('ashare_data_loader','matrix_manifest',matrix_dir/'task_052a_strict_matrix_manifest.json'),('ashare_data_loader','tensor_values',tensor_dir/'feature_tensor.npy'),('ashare_data_loader','tensor_validity',tensor_dir/'feature_validity_tensor.npy')]: ledger.open_artifact(path,component=component,dataset=dataset)
    feature_manifest=load_feature_manifest(config['feature_manifest']); factor=next(r for r in LocalFactorStore(config['factor_store_dir']).load_factors() if r.factor_id==config['probe_factor_id']); vocab=make_formula_vocab_from_manifest(feature_manifest);vm=StackVM(vocab)
    loader_obj=AShareDataLoader(matrix_cache_dir=matrix_dir,use_matrix_cache=True,feature_set_name=feature_manifest.feature_set_name,feature_set_manifest_path=config['feature_manifest'],research_end_date='20240530',holdout_start_date='20240531',label_horizon=2,canonical_feature_tensor_path=tensor_dir/'feature_tensor.npy',canonical_feature_validity_tensor_path=tensor_dir/'feature_validity_tensor.npy',device='cpu')
    loader=receipts.invoke('ashare_data_loader',loader_obj.load_data,input_artifacts={'matrix_manifest':matrix_dir/'task_052a_strict_matrix_manifest.json','tensor_manifest':tensor_dir/'task_053_v3_tensor_manifest.json'},output_artifacts={'projection_manifest':projection['manifest_path']},semantic_success=lambda r:r.feat_tensor is not None and r.feature_validity is not None)
    executed=receipts.invoke('stackvm',vm.execute_with_validity,factor.formula_tokens,loader.feat_tensor,loader.feature_validity,input_artifacts={'tensor_values':tensor_dir/'feature_tensor.npy','tensor_validity':tensor_dir/'feature_validity_tensor.npy'},output_artifacts=lambda r:_save_npz(output/'stackvm.npz',r),semantic_success=lambda r:r is not None)
    candidate=AlphaCandidateRecord(alpha_candidate_id=factor.factor_id,formula_hash=factor.formula_hash,formula_tokens=factor.formula_tokens,formula_names=factor.formula,source='task054c_probe',source_refs=[],feature_set_name=feature_manifest.feature_set_name,feature_version=factor.feature_version,operator_version=factor.operator_version,complexity=int((factor.metadata or {}).get('complexity',len(factor.formula_tokens))),lookback=factor.lookback_days,family_tags=[])
    proxy=receipts.invoke('proxy_evaluator',run_proxy_eval,[candidate],loader,max_candidates=1,max_dates=63,vocab=vocab,input_artifacts={'projection_manifest':projection['manifest_path']},output_artifacts=lambda r:_write_json(output/'proxy.json',{'rows':r[1],'summary':r[2]}),semantic_success=lambda r:not any(x.get('status')=='failed' for x in r[1]))
    cache_root=Path(config['cache_namespace']); batch=FormulaBatchEvaluator(FormulaBatchEvalConfig(data_dir=config['freeze_dir'],factor_store_dir=str(output/'batch_store'),report_dir=str(output/'batch'),output_dir=str(output/'batch'),matrix_cache_dir=str(matrix_dir),use_matrix_cache=True,device='cpu',feature_set_name=feature_manifest.feature_set_name,feature_set_manifest_path=config['feature_manifest'],research_end_date='20240530',holdout_start_date='20240531',label_horizon=2,eligible_date_hash=config['eligible_date_hash'],use_eval_cache=True,eval_cache_dir=str(cache_root/'formula_batch'),skip_existing=False,continue_on_error=False,canonical_feature_tensor_path=str(tensor_dir/'feature_tensor.npy'),canonical_feature_validity_tensor_path=str(tensor_dir/'feature_validity_tensor.npy'),research_computation_identity=projection['research_computation_identity']))
    request=FormulaEvalRequest(name=factor.factor_id,formula_tokens=factor.formula_tokens,formula_names=factor.formula,formula_hash=factor.formula_hash,complexity=int((factor.metadata or {}).get('complexity',len(factor.formula_tokens))),lookback=factor.lookback_days)
    batch_result=receipts.invoke('formula_batch_evaluator',batch.run,[request],input_artifacts={'projection_manifest':projection['manifest_path']},output_artifacts=lambda r:{'batch_result':r.paths['formula_batch_eval_result_path']},semantic_success=lambda r:r.status=='success' and all(x.status not in {'invalid','error'} for x in r.results))
    policy=load_validation_policy('task054_production_engineering_v1')
    materializer=FactorMaterializer(MaterializationInputs(data_freeze_dir=config['freeze_dir'],matrix_cache_dir=str(matrix_dir),feature_manifest_path=config['feature_manifest'],feature_tensor_path=str(tensor_dir/'feature_tensor.npy'),feature_validity_tensor_path=str(tensor_dir/'feature_validity_tensor.npy'),promotion_policy_path=config['promotion_policy'],research_end_date='20240530',label_horizon=2,research_eligible_date_mask_path=str(matrix_dir/'research_eligible_date_mask.npy'),eligibility_contract_hash=config['eligible_date_hash'],validation_policy_hash=policy.policy_hash,requested_embargo_size=25,research_computation_identity=projection['research_computation_identity']),cache_root/'materialization',device='cpu')
    materialized=receipts.invoke('factor_materializer',materializer.materialize,factor,input_artifacts={'projection_manifest':projection['manifest_path']},output_artifacts=lambda r:{'materialization_manifest':r.manifest_path},semantic_success=lambda r:r.status=='success')
    validation_args=['validate-factor','--factor-store-dir',config['factor_store_dir'],'--strict-factor-store','--factor-id',factor.factor_id,'--output-dir',str(output/'validation'),'--data-freeze-dir',config['freeze_dir'],'--matrix-cache-dir',str(matrix_dir),'--feature-set-manifest-path',config['feature_manifest'],'--feature-tensor-path',str(tensor_dir/'feature_tensor.npy'),'--feature-validity-tensor-path',str(tensor_dir/'feature_validity_tensor.npy'),'--materialization-manifest-path',materialized.manifest_path,'--strict-materialization','--research-end-date','20240530','--holdout-start-date','20240531','--label-horizon','2','--validation-policy','task054_production_engineering_v1','--train-size','756','--validation-size','126','--test-size','126','--step-size','126','--embargo-size','25']
    validation=receipts.invoke('validation_service',run_validation_service,validation_args,input_artifacts={'materialization_manifest':materialized.manifest_path,'projection_manifest':projection['manifest_path']},output_artifacts=lambda r:_write_json(output/'validation_service_result.json',r),semantic_success=lambda r:isinstance(r,dict) and r.get('status') in {'data_blocked','statistically_rejected','historical_replay_passed'})
    pool_path=output/'campaign_probe_pool.jsonl'; write_jsonl_artifact(pool_path,[{'factor_id':factor.factor_id,'formula_hash':factor.formula_hash,'formula_names':factor.formula,'rank':1,'final_score':0.0,'feature_version':factor.feature_version,'factor_store_dir':config['factor_store_dir'],'factor_values_path':''}],'alpha_validation_candidate_pool','task_054_c')
    campaign_dir=output/'campaign_store'; ingest_candidate_pool(campaign_dir,pool_path,validation_campaign_id=config['invocation_id'],max_candidates=1,shard_count=1,stratified=False); shard=plan_validation_shards(campaign_dir,output/'campaign_execution',validation_campaign_id=config['invocation_id'],shard_count=1)[0]; store=LocalValidationCampaignStore(campaign_dir)
    result_row={'factor_id':factor.factor_id,'status':validation['status'],'validation_blocker_count':validation.get('validation_blocker_count',0),'validation_summary':validation.get('validation_summary',{}),'source_candidate':{'factor_id':factor.factor_id,'formula_hash':factor.formula_hash},'paths':validation.get('paths',{})}
    result_path=receipts.invoke('campaign_store',store.record_shard_results,shard.shard_id,[result_row],input_artifacts={'validation_result':output/'validation_service_result.json'},output_artifacts=lambda r:{'shard_results':r})
    consolidation=receipts.invoke('consolidation',consolidate_validation_results,campaign_dir,output_dir=output/'consolidated',input_artifacts={'shard_results':result_path},output_artifacts=lambda r:{'consolidation':r['paths']['validation_campaign_consolidation_report_path']},semantic_success=lambda r:r.get('candidate_count')==1)
    semantic={'projection_identity':projection['research_computation_identity'],'factor_hash':_tensor_pair_hash(executed),'proxy_hash':canonical_hash(_proxy_semantic(proxy)),'batch_hash':canonical_hash(_batch_semantic(batch_result)),'materialization_values_sha256':sha256_file(materialized.values_path),'materialization_validity_sha256':sha256_file(materialized.validity_path),'validation_hash':canonical_hash(_validation_semantic(validation)),'consolidation_hash':canonical_hash({k:consolidation.get(k) for k in ('status','candidate_count','data_blocked_count','statistically_rejected_count','historical_replay_passed_count')}),'formula_batch_cache_hit':bool(batch_result.results[0].cache_hit),'materialization_cache_hit':bool(materialized.cache_hit),'diagnostic_hash':projection['diagnostic_hash']}
    semantic['research_semantic_hash']=canonical_hash({k:v for k,v in semantic.items() if k not in {'formula_batch_cache_hit','materialization_cache_hit','diagnostic_hash'}})
    payload={'schema_version':'task054c_execution_v1','status':'success','invocation_id':config['invocation_id'],'mutation':mutation,'path_name':config['path_name'],'source_kind':source_kind,'execution_kind':config['execution_kind'],'semantic':semantic,'receipt_root':receipts.previous,'ledger_root':ledger.previous,'projection_manifest_sha256':sha256_file(projection['manifest_path'])}; payload['content_hash']=canonical_hash(payload); _write_json(output/'execution.json',payload); return payload
def _save_npz(path:Path,result): np.savez(path,values=result[0].detach().cpu().numpy(),validity=result[1].detach().cpu().numpy()); return {'stackvm_result':path}
def _write_json(path:Path,payload): path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(payload,default=str,sort_keys=True)+'\n');return {'artifact':path}
def _tensor_pair_hash(r): return canonical_hash({'values':hashlib.sha256(r[0].detach().cpu().numpy().tobytes()).hexdigest(),'validity':hashlib.sha256(r[1].detach().cpu().numpy().tobytes()).hexdigest()})
def _proxy_semantic(r): return {'rows':[{k:v for k,v in x.items() if k not in {'runtime_ms','lineage_hash'}} for x in r[1]],'attempted':r[2].get('attempted'),'passed':r[2].get('passed'),'failed':r[2].get('failed'),'sampled_dates':r[2].get('sampled_dates')}
def _batch_semantic(r): return {'status':r.status,'results':[{'status':x.status,'score':x.score,'metrics_by_split':x.metrics_by_split,'gate_reasons':x.gate_reasons} for x in r.results]}
def _validation_semantic(r): return {'status':r.get('status'),'validation_blocker_count':r.get('validation_blocker_count'),'validation_summary':r.get('validation_summary'),'cell_counts':r.get('cell_counts')}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args(argv);print(json.dumps(run_worker(a.config),sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())

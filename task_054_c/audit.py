"""Supervisor-attested, tamper-evident reads and component receipts."""
from __future__ import annotations
import hashlib,inspect,json,os,time
from pathlib import Path
from typing import Any,Callable,Mapping
from .contracts import READ_LEDGER_SCHEMA,RECEIPT_SCHEMA
from .validators import canonical_hash,sha256_file

ALLOWED_COMPONENTS: dict[str,str] = {
 'strict_matrix_builder':'matrix_store.strict_engineering.StrictEngineeringPITMatrixBuilder.build',
 'v3_tensor_builder':'task_053_a.orchestrator.build_v3_tensor_generation',
 'strict_matrix_validator':'task_054_c.validators.validate_strict_matrix_generation',
 'v3_tensor_validator':'task_054_c.validators.validate_v3_tensor_generation',
 'research_projection_publisher':'task_054_c.research_view.publish_research_projection',
 'ashare_data_loader':'model_core.data_loader.AShareDataLoader.load_data',
 'stackvm':'model_core.vm.StackVM.execute_with_validity',
 'proxy_evaluator':'alpha_factory.proxy_eval.run_proxy_eval',
 'formula_batch_evaluator':'formula_batch_eval.evaluator.FormulaBatchEvaluator.run',
 'factor_materializer':'validation_lab.materialization.FactorMaterializer.materialize',
 'validation_service':'validation_lab.run_validation.run_validation_service',
 'campaign_store':'validation_campaign_store.registry.LocalValidationCampaignStore.record_shard_results',
 'consolidation':'validation_campaign_store.consolidate.consolidate_validation_results',
}

def fqn(fn:Callable[...,Any])->str:
    return f'{fn.__module__}.{fn.__qualname__}'

def source_hash(fn:Callable[...,Any])->str:
    path=inspect.getsourcefile(fn)
    if not path: raise RuntimeError('component_source_unavailable')
    return sha256_file(path)

class SupervisorLedger:
    def __init__(self,path:str|Path,*,invocation_id:str,projection_manifest:str|Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.invocation_id=invocation_id
        projection_path=Path(projection_manifest).resolve(strict=True); self.projection=json.loads(projection_path.read_text()); self.projection_root=projection_path.parent; self.sequence=0; self.previous='0'*64
    def open_artifact(self,path:str|Path,*,component:str,dataset:str)->Path:
        p=Path(path).resolve(strict=True); allowed=str(p).startswith(str(self.projection_root)+os.sep)
        relative_path=str(p.relative_to(self.projection_root)) if allowed else None
        row={'schema_version':READ_LEDGER_SCHEMA,'sequence':self.sequence+1,'invocation_id':self.invocation_id,'principal':'research','component':component,'dataset':dataset,'artifact_id':p.name,'relative_path':relative_path,'path_hash':hashlib.sha256(str(p).encode()).hexdigest(),'sha256':sha256_file(p),'date_range':[self.projection['research_date_start'],self.projection['research_date_end']],'policy_decision':'allow' if allowed else 'deny','previous_entry_hash':self.previous}
        row['entry_hash']=canonical_hash(row); self.sequence+=1; self.previous=row['entry_hash']
        with self.path.open('a') as h: h.write(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n');h.flush();os.fsync(h.fileno())
        if not allowed: raise PermissionError(f'research_read_outside_projection:{dataset}')
        return p

class ReceiptRecorder:
    def __init__(self,path:str|Path,*,invocation_id:str): self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.invocation_id=invocation_id;self.previous='0'*64
    def invoke(self,component:str,fn:Callable[...,Any],*args,input_artifacts:Mapping[str,str|Path],output_artifacts:Callable[[Any],Mapping[str,str|Path]]|Mapping[str,str|Path],semantic_success:Callable[[Any],bool]|None=None,**kwargs)->Any:
        expected=ALLOWED_COMPONENTS.get(component); actual=fqn(fn)
        if expected!=actual: raise RuntimeError(f'component_fqn_forbidden:{component}:{actual}')
        started=time.time_ns(); status='failed'; error=None; result=None; outputs={}
        try:
            result=fn(*args,**kwargs)
            if semantic_success and not semantic_success(result): raise RuntimeError(f'component_semantic_failure:{component}')
            mapping=output_artifacts(result) if callable(output_artifacts) else output_artifacts
            outputs={k:_artifact(v) for k,v in mapping.items()}; status='success'; return result
        except Exception as exc: error=str(exc); raise
        finally:
            row={'schema_version':RECEIPT_SCHEMA,'invocation_id':self.invocation_id,'component':component,'entrypoint':actual,'source_hash':source_hash(fn),'status':status,'error':error,'started_ns':started,'finished_ns':time.time_ns(),'input_artifacts':{k:_artifact(v) for k,v in input_artifacts.items()},'output_artifacts':outputs,'parent_receipt_hash':self.previous}
            row['receipt_hash']=canonical_hash(row); self.previous=row['receipt_hash']
            with self.path.open('a') as h:h.write(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n');h.flush();os.fsync(h.fileno())
def _artifact(path:str|Path)->dict[str,str]:
    p=Path(path); return {'artifact_id':p.name,'sha256':sha256_file(p)}

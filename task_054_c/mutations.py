"""Pre-registered governed freeze mutations and native A generations."""
from __future__ import annotations
import json,os,shutil,subprocess,tempfile
from pathlib import Path
from typing import Any
import numpy as np
from matrix_store.strict_engineering import StrictEngineeringPITMatrixBuilder,StrictEngineeringPITMatrixConfig
from task_053_a.orchestrator import build_v3_tensor_generation
from .validators import canonical_hash,sha256_file,validate_strict_matrix_generation,validate_v3_tensor_generation

def build_mutation_generations(*,freeze_root:str|Path,universe_root:str|Path,baseline_matrix:str|Path,baseline_tensor:str|Path,feature_manifest:str|Path,candidate_pool:str|Path,output_root:str|Path)->dict[str,Any]:
    freeze_root=Path(freeze_root); baseline_matrix=Path(baseline_matrix); baseline_tensor=Path(baseline_tensor); root=Path(output_root); root.mkdir(parents=True,exist_ok=True)
    dates=json.loads((baseline_matrix/'trade_dates.json').read_text()); stocks=json.loads((baseline_matrix/'ts_codes.json').read_text())
    membership=np.load(baseline_matrix/'membership.npy',mmap_mode='r'); close_valid=np.load(baseline_matrix/'close_validity.npy',mmap_mode='r'); adj_valid=np.load(baseline_matrix/'adjustment_validity.npy',mmap_mode='r'); signal=np.load(baseline_matrix/'signal_candidate_cells.npy',mmap_mode='r')
    cells={}
    for kind,candidates in {'inside_cutoff':[d for d in dates if d<='20240528'][::-1],'post_cutoff':[d for d in dates if d>'20240530']}.items():
        found=None
        for date in candidates:
            di=dates.index(date); mask=membership[:,di]&close_valid[:,di]&adj_valid[:,di]
            if kind=='inside_cutoff': mask &= signal[:,di]
            positions=np.flatnonzero(mask)
            if positions.size: found={'ts_code':stocks[int(positions[0])],'trade_date':date,'field':'close','delta':0.125,'probe_factor_id':'factor_369358b247706fb5','dependency':'RET_1D.adjusted_close'}; break
        if not found: raise RuntimeError(f'mutation_cell_unavailable:{kind}')
        cells[kind]=found
    generations={'baseline':{'freeze_dir':str(freeze_root),'matrix_dir':str(baseline_matrix),'tensor_dir':str(baseline_tensor)}}
    for kind in ('post_cutoff','inside_cutoff'):
        freeze=_copy_and_mutate_freeze(freeze_root,root/kind/'freeze_generations',cells[kind],kind)
        matrix_result=StrictEngineeringPITMatrixBuilder(StrictEngineeringPITMatrixConfig(research_observable_cutoff='20240530',target_endpoint_horizon_trade_days=2,min_cross_section_breadth=30)).build(governed_freeze_dir=freeze,historical_universe_dir=universe_root,output_root=root/kind/'matrix_generations_a')
        tensor=build_v3_tensor_generation(matrix_dir=matrix_result.generation_dir,feature_manifest_path=feature_manifest,output_root=root/kind/'tensor_generations_a',candidate_pool_path=candidate_pool)
        matrix=validate_strict_matrix_generation(matrix_result.generation_dir); tensor_v=validate_v3_tensor_generation(tensor['generation_dir'],matrix=matrix)
        generations[kind]={'freeze_dir':str(freeze),'matrix_dir':matrix['root'],'tensor_dir':tensor_v['root'],'freeze_content_hash':json.loads(next(Path(freeze).glob('*freeze*manifest*.json')).read_text())['content_hash'],'matrix_content_hash':matrix['content_hash'],'tensor_content_hash':tensor_v['content_hash']}
    semantic={'schema_version':'task054c_mutation_generations_v1','pre_registered_before_outcome_read':True,'probe_formula_dependency':'RET_1D.adjusted_close','cells':cells,'generations':generations}
    semantic['content_hash']=canonical_hash(semantic); path=root/'task054c_mutation_generations.json'; path.write_text(json.dumps(semantic,indent=2,sort_keys=True)+'\n'); return semantic|{'manifest_path':str(path)}
def _copy_and_mutate_freeze(source:Path,output_root:Path,cell:dict[str,Any],kind:str)->Path:
    manifest_path=next(source.glob('*freeze*manifest*.json')); manifest=json.loads(manifest_path.read_text()); output_root.parent.mkdir(parents=True,exist_ok=True); staging=Path(tempfile.mkdtemp(prefix=f'.freeze_{kind}.',dir=output_root.parent))
    shutil.rmtree(staging); _reflink_tree(source,staging)
    target_artifact=next(a for a in manifest['artifacts'] if a['logical_name']=='daily_bars'); bars=staging/target_artifact['relative_path']; tmp=bars.with_suffix('.mutation'); changed=0
    with bars.open() as src,tmp.open('w') as dst:
        for line in src:
            row=json.loads(line)
            if row.get('ts_code')==cell['ts_code'] and row.get('trade_date')==cell['trade_date']:
                row[cell['field']]=float(row[cell['field']])+float(cell['delta']); line=json.dumps(row,sort_keys=True)+'\n'; changed+=1
            dst.write(line)
    if changed!=1: raise RuntimeError(f'mutation_cardinality:{kind}:{changed}')
    os.replace(tmp,bars); artifacts=[]
    for item in manifest['artifacts']:
        row=dict(item); p=staging/row['relative_path']; row['sha256']=sha256_file(p); row['size_bytes']=p.stat().st_size; artifacts.append(row)
    manifest['artifacts']=artifacts; manifest['artifacts_by_name']={r['logical_name']:r for r in artifacts}; manifest['mutation_contract']={'task':'054-C','kind':kind,'cell':cell}; manifest['content_hash']=canonical_hash({'semantic_hash':manifest.get('semantic_hash'),'source_lineage_manifest_sha256':manifest.get('source_lineage_manifest_sha256'),'artifacts':[{k:r.get(k) for k in ('logical_name','relative_path','sha256','size_bytes')} for r in artifacts],'mutation_contract':manifest['mutation_contract']}); manifest['generation_id']=f"freeze_054c_{manifest['content_hash'][:24]}"; copied_manifest=staging/manifest_path.name; copied_manifest.chmod(0o644); copied_manifest.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    output_root.mkdir(parents=True,exist_ok=True); target=output_root/manifest['generation_id']
    if target.exists(): shutil.rmtree(staging)
    else: os.replace(staging,target)
    return target
def _reflink_tree(source:Path,target:Path)->None:
    result=subprocess.run(['cp','-a','--reflink=always',str(source),str(target)],capture_output=True,text=True)
    if result.returncode:
        shutil.rmtree(target,ignore_errors=True)
        shutil.copytree(source,target)

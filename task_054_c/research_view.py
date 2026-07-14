"""Physical bounded research and diagnostic projections."""
from __future__ import annotations
import json,os,tempfile
from pathlib import Path
from typing import Any
import numpy as np
from .validators import canonical_hash,sha256_file

def publish_research_projection(*, matrix_root:str|Path,tensor_root:str|Path,output_root:str|Path,research_end_date:str='20240530') -> dict[str,Any]:
    matrix_root=Path(matrix_root); tensor_root=Path(tensor_root); dates=json.loads((matrix_root/'trade_dates.json').read_text()); stocks=json.loads((matrix_root/'ts_codes.json').read_text())
    source_matrix=json.loads((matrix_root/'task_052a_strict_matrix_manifest.json').read_text()); source_tensor=json.loads((tensor_root/'task_053_v3_tensor_manifest.json').read_text())
    max_signal=str(source_matrix['max_legal_signal_date']); indices=[i for i,d in enumerate(dates) if d<=max_signal]
    if not indices or indices!=list(range(len(indices))): raise RuntimeError('research_projection_requires_prefix_axis')
    count=len(indices); projected_dates=dates[:count]; root=Path(output_root); root.parent.mkdir(parents=True,exist_ok=True); staging=Path(tempfile.mkdtemp(prefix='.research_projection.',dir=root.parent))
    matrix_out=staging/'matrix'; tensor_out=staging/'tensor'; matrix_out.mkdir(); tensor_out.mkdir()
    partition_hashes={}
    for name in source_matrix['partition_sha256']:
        source=matrix_root/name; target=matrix_out/name
        if source.suffix=='.npy':
            arr=np.load(source,mmap_mode='r',allow_pickle=False)
            if arr.ndim>=1 and arr.shape[-1]==len(dates): _write_npy(target,arr[..., :count])
            else: _link_or_copy(source,target)
        elif name=='trade_dates.json': target.write_text(json.dumps(projected_dates,sort_keys=True)+'\n')
        else: _link_or_copy(source,target)
        partition_hashes[name]=sha256_file(target)
    projected_matrix={**source_matrix,'schema_version':'task054c_research_matrix_projection_v1','generation_id':None,'content_hash':None,'shape':[len(stocks),count],'date_axis_hash':_hash_lines(projected_dates),'partition_sha256':partition_hashes,'raw_truncated_before_compute':True,'physical_research_projection':True,'research_firewall_attested':False,'source_full_generation':{'content_hash':source_matrix['content_hash'],'manifest_sha256':sha256_file(matrix_root/'task_052a_strict_matrix_manifest.json')},'max_projection_date':projected_dates[-1]}
    projected_matrix['content_hash']=canonical_hash({k:v for k,v in projected_matrix.items() if k not in {'generation_id','content_hash','created_at','artifact_metadata'}}); projected_matrix['generation_id']=f"research_matrix_{projected_matrix['content_hash'][:24]}"
    (matrix_out/'task_052a_strict_matrix_manifest.json').write_text(json.dumps(projected_matrix,indent=2,sort_keys=True)+'\n')
    values=np.load(tensor_root/'feature_tensor.npy',mmap_mode='r'); validity=np.load(tensor_root/'feature_validity_tensor.npy',mmap_mode='r')
    _write_npy(tensor_out/'feature_tensor.npy',values[..., :count]); _write_npy(tensor_out/'feature_validity_tensor.npy',validity[..., :count])
    projected_source=dict(source_tensor.get('source') or {})
    projected_source.update({'matrix_content_hash':projected_matrix['content_hash'],'matrix_manifest_sha256':sha256_file(matrix_out/'task_052a_strict_matrix_manifest.json')})
    projected_tensor={**source_tensor,'schema_version':'task054c_research_tensor_projection_v1','generation_id':None,'content_hash':None,'shape':[values.shape[0],values.shape[1],count],'date_axis_hash':projected_matrix['date_axis_hash'],'values_sha256':sha256_file(tensor_out/'feature_tensor.npy'),'validity_sha256':sha256_file(tensor_out/'feature_validity_tensor.npy'),'source':projected_source,'physical_research_projection':True,'source_full_generation':{'content_hash':source_tensor['content_hash'],'manifest_sha256':sha256_file(tensor_root/'task_053_v3_tensor_manifest.json')},'max_projection_date':projected_dates[-1]}
    projected_tensor['content_hash']=canonical_hash({k:v for k,v in projected_tensor.items() if k not in {'generation_id','content_hash','created_at','artifact_metadata'}}); projected_tensor['generation_id']=f"research_tensor_{projected_tensor['content_hash'][:24]}"
    (tensor_out/'task_053_v3_tensor_manifest.json').write_text(json.dumps(projected_tensor,indent=2,sort_keys=True)+'\n')
    diagnostic_mask=np.asarray([d>research_end_date for d in dates]); diagnostic_hash=canonical_hash({'dates':[d for d in dates if d>research_end_date],'values_sha256':_slice_hash(values,diagnostic_mask),'validity_sha256':_slice_hash(validity,diagnostic_mask)})
    semantic={'schema_version':'task054c_physical_research_projection_v1','matrix_content_hash':projected_matrix['content_hash'],'tensor_content_hash':projected_tensor['content_hash'],'research_computation_identity':canonical_hash({'matrix_partitions':partition_hashes,'tensor_values':projected_tensor['values_sha256'],'tensor_validity':projected_tensor['validity_sha256'],'date_axis_hash':projected_matrix['date_axis_hash'],'eligible_date_hash':source_matrix['eligible_date_hash'],'target_contract':source_matrix['target_contract']}),'diagnostic_hash':diagnostic_hash,'research_date_start':projected_dates[0],'research_date_end':projected_dates[-1],'research_date_count':count,'supervisor_attested_tamper_evident':True,'cryptographically_unforgeable':False}
    content_hash=canonical_hash(semantic); gid=f'research_projection_{content_hash[:24]}'; target=root/'generations'/gid; target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists(): _remove(staging)
    else: os.replace(staging,target)
    manifest=semantic|{'generation_id':gid,'content_hash':content_hash,'matrix_root':'matrix','tensor_root':'tensor'}; (target/'research_projection_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    root.mkdir(parents=True,exist_ok=True); tmp=root/'.current.tmp'; tmp.write_text(json.dumps({'generation_id':gid,'content_hash':content_hash,'manifest':f'generations/{gid}/research_projection_manifest.json'},sort_keys=True)+'\n'); os.replace(tmp,root/'current.json')
    return manifest|{'generation_dir':str(target),'manifest_path':str(target/'research_projection_manifest.json'),'matrix_dir':str(target/'matrix'),'tensor_dir':str(target/'tensor')}

def validate_research_projection(path:str|Path)->dict[str,Any]:
    p=Path(path); m=json.loads(p.read_text()); root=p.parent
    if canonical_hash({k:v for k,v in m.items() if k not in {'generation_id','content_hash','matrix_root','tensor_root'}})!=m['content_hash']: raise RuntimeError('research_projection_content_hash_mismatch')
    matrix_root=root/m['matrix_root'];tensor_root=root/m['tensor_root']
    matrix=json.loads((matrix_root/'task_052a_strict_matrix_manifest.json').read_text());tensor=json.loads((tensor_root/'task_053_v3_tensor_manifest.json').read_text())
    for name,digest in (matrix.get('partition_sha256') or {}).items():
        if sha256_file(matrix_root/name)!=digest: raise RuntimeError(f'research_projection_matrix_partition_mismatch:{name}')
    if sha256_file(tensor_root/'feature_tensor.npy')!=tensor.get('values_sha256') or sha256_file(tensor_root/'feature_validity_tensor.npy')!=tensor.get('validity_sha256'): raise RuntimeError('research_projection_tensor_partition_mismatch')
    if matrix.get('content_hash')!=m['matrix_content_hash'] or tensor.get('content_hash')!=m['tensor_content_hash']: raise RuntimeError('research_projection_nested_content_mismatch')
    dates=json.loads((matrix_root/'trade_dates.json').read_text())
    if dates[-1]!=m['research_date_end'] or any(d>m['research_date_end'] for d in dates): raise RuntimeError('research_projection_axis_unbounded')
    return m|{'manifest_path':str(p),'generation_dir':str(root),'matrix_dir':str(root/m['matrix_root']),'tensor_dir':str(root/m['tensor_root'])}

def load_research_projection_manifest(path:str|Path)->dict[str,Any]:
    p=Path(path);m=json.loads(p.read_text());root=p.parent
    if canonical_hash({k:v for k,v in m.items() if k not in {'generation_id','content_hash','matrix_root','tensor_root'}})!=m['content_hash']: raise RuntimeError('research_projection_content_hash_mismatch')
    dates=json.loads((root/m['matrix_root']/'trade_dates.json').read_text())
    if not dates or dates[-1]!=m['research_date_end'] or len(dates)!=int(m['research_date_count']): raise RuntimeError('research_projection_axis_unbounded')
    return m|{'manifest_path':str(p),'generation_dir':str(root),'matrix_dir':str(root/m['matrix_root']),'tensor_dir':str(root/m['tensor_root'])}

def _write_npy(path:Path,array:np.ndarray)->None:
    out=np.lib.format.open_memmap(path,mode='w+',dtype=array.dtype,shape=array.shape)
    step=64 if array.ndim>=3 else 256
    if array.ndim>=1:
        for start in range(0,array.shape[0],step): out[start:start+step]=array[start:start+step]
    else: out[...]=array
    out.flush(); del out

def _link_or_copy(source:Path,target:Path)->None:
    try: os.link(source,target)
    except OSError:
        import shutil; shutil.copy2(source,target)

def _hash_lines(values:list[str])->str:
    import hashlib
    h=hashlib.sha256()
    for v in values:h.update(v.encode());h.update(b'\n')
    return h.hexdigest()
def _slice_hash(array:np.ndarray,mask:np.ndarray)->str:
    import hashlib
    h=hashlib.sha256(); view=np.ascontiguousarray(array[...,mask]); h.update(view.tobytes()); return h.hexdigest()
def _remove(root:Path)->None:
    import shutil; shutil.rmtree(root,ignore_errors=True)

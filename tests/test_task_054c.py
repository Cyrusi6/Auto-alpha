import json
from pathlib import Path
import numpy as np
import pytest
from model_core.vm import StackVM
from task_054_c.bundle import validate_bundle
from task_054_c.factor_store import validate_normalized_replay_store
from task_054_c.research_view import validate_research_projection
from task_054_c.seal import validate_pre_gpu_seal
from task_054_c.validators import canonical_hash,sha256_file,validate_strict_matrix_generation,validate_v3_tensor_generation

def test_formula_lookback_uses_max_raw_lag_units():
    vm=StackVM(); feature=vm.vocab.encode_name('RET_1D'); mean=vm.vocab.encode_name('TS_MEAN10')
    assert vm.formula_lookback([feature],{'RET_1D':1})==1
    assert vm.formula_lookback([feature,mean],{'RET_1D':1})==10

def test_native_matrix_validator_rejects_missing_cell_mask(tmp_path:Path):
    shape=(637,6417); dates=[f'{i:08d}' for i in range(shape[1])]; stocks=[f'{i:06d}.SZ' for i in range(shape[0])]
    (tmp_path/'trade_dates.json').write_text(json.dumps(dates));(tmp_path/'ts_codes.json').write_text(json.dumps(stocks))
    names=['signal_candidate_cells.npy','validation_common_cells.npy','target_available.npy','research_eligible_date_mask.npy']
    for name in names:
        arr=np.zeros(shape if name!='research_eligible_date_mask.npy' else (shape[1],),dtype=np.bool_);np.save(tmp_path/name,arr)
    parts={p.name:sha256_file(p) for p in tmp_path.iterdir()}
    manifest={'content_hash':'x','shape':list(shape),'stock_axis_hash':'a'*64,'date_axis_hash':'b'*64,'eligible_date_hash':'c'*64,'max_legal_signal_date':'20240528','max_legal_endpoint_date':'20240530','partition_sha256':parts}
    (tmp_path/'task_052a_strict_matrix_manifest.json').write_text(json.dumps(manifest))
    validate_strict_matrix_generation(tmp_path)
    (tmp_path/'signal_candidate_cells.npy').unlink()
    with pytest.raises(RuntimeError,match='matrix_partition_mismatch|matrix_required_partition_missing'): validate_strict_matrix_generation(tmp_path)

def test_tensor_validator_rejects_cross_matrix_swap(tmp_path:Path):
    values=np.zeros((2,3,4),np.float32); validity=np.zeros_like(values,dtype=np.bool_);np.save(tmp_path/'feature_tensor.npy',values);np.save(tmp_path/'feature_validity_tensor.npy',validity)
    manifest={'content_hash':'tensor','shape':[2,3,4],'stock_axis_hash':'s','date_axis_hash':'d','feature_axis_hash':'f','values_sha256':sha256_file(tmp_path/'feature_tensor.npy'),'validity_sha256':sha256_file(tmp_path/'feature_validity_tensor.npy'),'source':{'matrix_content_hash':'matrix-a'}}
    (tmp_path/'task_053_v3_tensor_manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='tensor_matrix_lineage_mismatch'): validate_v3_tensor_generation(tmp_path,matrix={'content_hash':'matrix-b','stock_axis_hash':'s','date_axis_hash':'d'})

def test_bundle_rejects_tampering(tmp_path:Path):
    semantic={'schema_version':'x','exact20_ids':[str(i) for i in range(20)]};payload=semantic|{'generation_id':'g','content_hash':canonical_hash(semantic),'artifact_paths':{}};p=tmp_path/'bundle.json';payload['exact20_ids'][0]='tampered';p.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError,match='bundle_content_hash_mismatch'):validate_bundle(p)

def test_pre_gpu_seal_rejects_tampering(tmp_path:Path):
    payload={'schema_version':'task054c_pre_gpu_gate_seal_v1','status':'sealed','stages':{},'bundle_hash':'b','eligible_date_hash':'e','exact20_identity_root':'i','certification_ready':False,'portfolio_ready':False,'paper_ready':False,'live_ready':False}
    payload['seal_hash']=canonical_hash(payload);path=tmp_path/'task054c_pre_gpu_gate_seal.json'
    payload['bundle_hash']='forged';path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError,match='pre_gpu_seal_invalid'):validate_pre_gpu_seal(path)

def test_research_projection_validator_recomputes_native_partitions(tmp_path:Path):
    matrix=tmp_path/'matrix';tensor=tmp_path/'tensor';matrix.mkdir();tensor.mkdir()
    (matrix/'trade_dates.json').write_text(json.dumps(['20240527','20240528']))
    np.save(matrix/'target_available.npy',np.ones((2,2),dtype=np.bool_))
    matrix_manifest={'content_hash':'matrix','partition_sha256':{'trade_dates.json':sha256_file(matrix/'trade_dates.json'),'target_available.npy':sha256_file(matrix/'target_available.npy')}}
    (matrix/'task_052a_strict_matrix_manifest.json').write_text(json.dumps(matrix_manifest))
    np.save(tensor/'feature_tensor.npy',np.zeros((2,1,2),dtype=np.float32));np.save(tensor/'feature_validity_tensor.npy',np.ones((2,1,2),dtype=np.bool_))
    tensor_manifest={'content_hash':'tensor','values_sha256':sha256_file(tensor/'feature_tensor.npy'),'validity_sha256':sha256_file(tensor/'feature_validity_tensor.npy')}
    (tensor/'task_053_v3_tensor_manifest.json').write_text(json.dumps(tensor_manifest))
    semantic={'schema_version':'task054c_research_projection_v1','research_computation_identity':'r','research_date_start':'20240527','research_date_end':'20240528','research_date_count':2,'matrix_content_hash':'matrix','tensor_content_hash':'tensor','diagnostic_hash':'d','supervisor_attested_tamper_evident':True,'cryptographically_unforgeable':False}
    manifest=semantic|{'generation_id':'g','content_hash':canonical_hash(semantic),'matrix_root':'matrix','tensor_root':'tensor'};path=tmp_path/'research_projection_manifest.json';path.write_text(json.dumps(manifest));validate_research_projection(path)
    np.save(matrix/'target_available.npy',np.zeros((2,2),dtype=np.bool_))
    with pytest.raises(RuntimeError,match='research_projection_matrix_partition_mismatch'):validate_research_projection(path)

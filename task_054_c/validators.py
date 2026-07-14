"""Authoritative validators for native production artifacts."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any
import numpy as np

EXPECTED_MATRIX_HASH = "550c09a98808df5b5d2c8009e80a470cd2acc58ea4ec2d8ce6ff555add224e02"
EXPECTED_TENSOR_HASH = "08080009ff74f40acf5fc6698e09af3f4d41cf75d6a9b68e5fc6758a16f50610"
EXPECTED_SEMANTICS_HASH = "8114a4728a3b126cdbfe79343c318da992649db6a43096c458d762d20f63abd0"
EXPECTED_OVERLAY_HASH = "06781eb9d8a99f1adc3c930014ef0f20350fe84aa1c1a99f591a4eef8e028525"
EXPECTED_ELIGIBLE_HASH = "748d9120bf238689e9caf7c587530a1f3dc710bf411ee6895f2873acad10987e"

def sha256_file(path: str | Path) -> str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()

def validate_strict_matrix_generation(root: str | Path, *, expected_content_hash: str | None=None) -> dict[str,Any]:
    root=Path(root); path=root/'task_052a_strict_matrix_manifest.json'
    m=json.loads(path.read_text())
    if expected_content_hash and m.get('content_hash') != expected_content_hash: raise RuntimeError('matrix_content_hash_mismatch')
    if m.get('shape') != [637,6417]: raise RuntimeError('matrix_shape_mismatch')
    if m.get('research_holdout_firewall_enabled') is True or m.get('research_firewall_ready') is True: raise RuntimeError('matrix_self_attested_firewall')
    if not m.get('physical_research_projection') and m.get('raw_truncated_before_compute') is True: raise RuntimeError('matrix_false_raw_truncation_attestation')
    required={'signal_candidate_cells.npy','validation_common_cells.npy','target_available.npy','research_eligible_date_mask.npy','trade_dates.json','ts_codes.json'}
    parts=m.get('partition_sha256') or {}
    if not required.issubset(parts): raise RuntimeError(f'matrix_required_partition_missing:{sorted(required-set(parts))}')
    for name,digest in parts.items():
        p=root/name
        if not p.is_file() or sha256_file(p)!=digest: raise RuntimeError(f'matrix_partition_mismatch:{name}')
    dates=json.loads((root/'trade_dates.json').read_text()); stocks=json.loads((root/'ts_codes.json').read_text())
    if len(stocks)!=637 or len(dates)!=6417: raise RuntimeError('matrix_axis_length_mismatch')
    return {'artifact_type':'strict_matrix','root':str(root),'manifest_path':str(path),'manifest_sha256':sha256_file(path),'content_hash':m['content_hash'],'shape':m['shape'],'stock_axis_hash':m['stock_axis_hash'],'date_axis_hash':m['date_axis_hash'],'eligible_date_hash':m['eligible_date_hash'],'max_legal_signal_date':m['max_legal_signal_date'],'max_legal_endpoint_date':m['max_legal_endpoint_date'],'partition_count':len(parts),'manifest':m}

def validate_v3_tensor_generation(root: str | Path, *, matrix: dict[str,Any] | None=None, expected_content_hash: str | None=None) -> dict[str,Any]:
    root=Path(root); path=root/'task_053_v3_tensor_manifest.json'; m=json.loads(path.read_text())
    if expected_content_hash and m.get('content_hash')!=expected_content_hash: raise RuntimeError('tensor_content_hash_mismatch')
    vp=root/'feature_tensor.npy'; mp=root/'feature_validity_tensor.npy'
    if sha256_file(vp)!=m.get('values_sha256') or sha256_file(mp)!=m.get('validity_sha256'): raise RuntimeError('tensor_partition_mismatch')
    values=np.load(vp,mmap_mode='r'); validity=np.load(mp,mmap_mode='r')
    if list(values.shape)!=m.get('shape') or values.shape!=validity.shape or values.dtype!=np.float32 or validity.dtype!=np.bool_: raise RuntimeError('tensor_shape_dtype_mismatch')
    if matrix:
        source=m.get('source') or {}
        if source.get('matrix_content_hash')!=matrix['content_hash'] or m.get('stock_axis_hash')!=matrix['stock_axis_hash'] or m.get('date_axis_hash')!=matrix['date_axis_hash']: raise RuntimeError('tensor_matrix_lineage_mismatch')
    return {'artifact_type':'v3_tensor','root':str(root),'manifest_path':str(path),'manifest_sha256':sha256_file(path),'content_hash':m['content_hash'],'shape':m['shape'],'stock_axis_hash':m['stock_axis_hash'],'date_axis_hash':m['date_axis_hash'],'feature_axis_hash':m['feature_axis_hash'],'values_sha256':m['values_sha256'],'validity_sha256':m['validity_sha256'],'manifest':m}

def resolve_and_validate_overlay(root: str | Path, *, expected_content_hash: str | None=None) -> dict[str,Any]:
    root=Path(root); pointer=json.loads((root/'current.json').read_text()); manifest_path=root/pointer['manifest']; m=json.loads(manifest_path.read_text()); records=manifest_path.parent/m['records_file']
    if pointer.get('content_hash')!=m.get('content_hash'): raise RuntimeError('overlay_pointer_mismatch')
    if expected_content_hash and m.get('content_hash')!=expected_content_hash: raise RuntimeError('overlay_content_hash_mismatch')
    if sha256_file(records)!=m.get('records_sha256'): raise RuntimeError('overlay_records_mismatch')
    rows=[json.loads(x) for x in records.read_text().splitlines() if x.strip()]
    if len(rows)!=m.get('record_count'): raise RuntimeError('overlay_record_count_mismatch')
    return {'artifact_type':'normalized_overlay','root':str(root),'manifest_path':str(manifest_path),'manifest_sha256':sha256_file(manifest_path),'records_path':str(records),'records_sha256':m['records_sha256'],'content_hash':m['content_hash'],'record_count':len(rows),'records':rows,'manifest':m}

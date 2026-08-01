"""Read-only 187-candidate lookback unit consistency audit."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from feature_factory import build_feature_semantics_map,make_formula_vocab_from_manifest
from feature_factory.builder import load_feature_manifest
from model_core.vm import StackVM
from .validators import canonical_hash,sha256_file

def audit_lookback_units(*,candidate_pool:str|Path,feature_manifest:str|Path,overlay:dict[str,Any],output_path:str|Path)->dict[str,Any]:
    rows=[json.loads(x) for x in Path(candidate_pool).read_text().splitlines() if x.strip()]; manifest=load_feature_manifest(feature_manifest);vm=StackVM(make_formula_vocab_from_manifest(manifest));semantics=build_feature_semantics_map(manifest);overlay_by_id={r['factor_id']:r for r in overlay['records']}; mismatches=[]
    for row in rows:
        tokens=[vm.vocab.encode_name(x) for x in row['formula_names']]; calc=vm.formula_semantics(tokens,semantics); stored=overlay_by_id.get(row.get('factor_id'))
        if stored and (int(stored['lookback_days'])!=int(calc.max_raw_lag) or int(stored['canonical_required_observations'])!=int(calc.required_observations)): mismatches.append({'factor_id':row.get('factor_id'),'overlay_lookback':stored['lookback_days'],'canonical_max_raw_lag':calc.max_raw_lag,'overlay_required_observations':stored['canonical_required_observations'],'canonical_required_observations':calc.required_observations})
    payload={'schema_version':'task054c_lookback_unit_audit_v1','candidate_count':len(rows),'overlay_record_count':len(overlay_by_id),'lookback_unit':'max_raw_lag','required_observations_rule':'max_raw_lag_plus_one','mismatch_count':len(mismatches),'mismatches':mismatches,'addendum_required':bool(mismatches),'source_candidate_pool_sha256':sha256_file(candidate_pool),'overlay_content_hash':overlay['content_hash']};payload['content_hash']=canonical_hash(payload);Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n');return payload

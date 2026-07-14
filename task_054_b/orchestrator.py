"""Stage-specific production evidence validation for Task 054-B."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from artifact_schema.validator import validate_artifact
from artifact_schema.writer import attach_artifact_metadata
from validation_campaign_store.replay_evidence import validate_task054_replay_evidence


TASK054B_STAGE_ORDER = (
    "governed_source",
    "strict_matrix",
    "v3_tensor",
    "production_firewall_sentinel",
    "identity_forensic",
    "four_gpu_replay",
)
TASK054B_REQUIRED_DATASETS = frozenset({"suspensions", "stock_st", "namechange"})
TASK054B_REQUIRED_MATRIX_ARRAYS = frozenset(
    {
        "signal_candidate_cells",
        "validation_common_cells",
        "target_available",
        "membership",
        "active",
        "research_eligible_date_mask",
    }
)
TASK054B_REQUIRED_COMPONENTS = frozenset(
    {
        "ashare_data_loader",
        "strict_engineering_pit_matrix_builder",
        "v3_tensor_builder",
        "stackvm",
        "factor_materializer",
        "alpha_proxy",
        "formula_batch_evaluator",
        "validation_lab",
        "consolidation",
    }
)
TASK054B_PATHS = ("raw_local", "raw_scheduler", "matrix_local", "matrix_scheduler")
TASK054B_MUTATIONS = ("baseline", "post_cutoff", "inside_cutoff")
TASK054B_RESEARCH_OUTPUT_KEYS = frozenset(
    {
        "tensor_values_sha256",
        "tensor_validity_sha256",
        "factor_sha256",
        "proxy_sha256",
        "full_eval_sha256",
        "materialization_quality_sha256",
        "validation_status_sha256",
        "consolidation_status_sha256",
    }
)


@dataclass(frozen=True)
class Task054BStageContract:
    name: str
    proof_path: str
    dependencies: tuple[str, ...] = ()
    expected_candidate_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.name not in TASK054B_STAGE_ORDER:
            raise ValueError(f"unknown Task 054-B stage:{self.name}")
        for dependency in self.dependencies:
            if dependency not in TASK054B_STAGE_ORDER:
                raise ValueError(f"unknown Task 054-B dependency:{dependency}")
            if TASK054B_STAGE_ORDER.index(dependency) >= TASK054B_STAGE_ORDER.index(self.name):
                raise ValueError(f"non-DAG Task 054-B dependency:{self.name}:{dependency}")


class Task054BProductionDAG:
    def __init__(self, contracts: Iterable[Task054BStageContract], output_dir: str | Path):
        self.contracts = {contract.name: contract for contract in contracts}
        self.output_dir = Path(output_dir)
        if set(self.contracts) != set(TASK054B_STAGE_ORDER):
            missing = sorted(set(TASK054B_STAGE_ORDER) - set(self.contracts))
            extra = sorted(set(self.contracts) - set(TASK054B_STAGE_ORDER))
            raise ValueError(f"Task 054-B stage set mismatch:missing={missing}:extra={extra}")
        for contract in self.contracts.values():
            contract.validate()

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        verified_payloads: dict[str, dict[str, Any]] = {}
        stages: dict[str, dict[str, Any]] = {}
        blockers: list[str] = []
        for name in TASK054B_STAGE_ORDER:
            contract = self.contracts[name]
            failed_dependencies = [item for item in contract.dependencies if item not in verified_payloads]
            if failed_dependencies:
                stage_blockers = [f"dependency_not_verified:{item}" for item in failed_dependencies]
                stages[name] = {"stage": name, "verified": False, "blockers": stage_blockers}
                blockers.extend(f"{name}:{item}" for item in stage_blockers)
                continue
            try:
                proof = validate_task054b_stage(
                    name,
                    contract.proof_path,
                    completed_stages=verified_payloads,
                    expected_candidate_ids=contract.expected_candidate_ids,
                )
            except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as error:
                stages[name] = {"stage": name, "verified": False, "blockers": [str(error)]}
                blockers.append(f"{name}:{error}")
                continue
            verified_payloads[name] = proof
            stages[name] = {
                "stage": name,
                "verified": True,
                "proof_sha256": _sha256_file(Path(contract.proof_path)),
                "content_hash": proof["content_hash"],
                "validation_summary": proof.get("validation_summary", {}),
            }
        complete = not blockers and len(verified_payloads) == len(TASK054B_STAGE_ORDER)
        semantic = {
            "schema_version": "task_054b_production_dag_v1",
            "status": (
                "task054b_engineering_baseline_completed_historical_selection_contaminated_certification_blocked"
                if complete
                else "task054b_engineering_baseline_blocked"
            ),
            "stages": stages,
            "engineering_blockers": blockers,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
            "certification_queue_count": 0,
            "portfolio_queue_count": 0,
            "paper_queue_count": 0,
            "live_queue_count": 0,
        }
        semantic["content_hash"] = _canonical_hash(semantic)
        report = attach_artifact_metadata(semantic, "task_054b_production_dag_report", "task_054_b")
        _atomic_json(self.output_dir / "task_054b_production_dag_report.json", report)
        return report


def validate_task054b_stage(
    stage: str,
    proof_path: str | Path,
    *,
    completed_stages: Mapping[str, Mapping[str, Any]] | None = None,
    expected_candidate_ids: Sequence[str] = (),
) -> dict[str, Any]:
    if stage not in TASK054B_STAGE_ORDER:
        raise ValueError(f"unknown Task 054-B stage:{stage}")
    path = Path(proof_path)
    proof = _load_json(path)
    if proof.get("stage") != stage:
        raise RuntimeError(f"Task 054-B stage identity mismatch:{stage}")
    if proof.get("status") != "complete":
        raise RuntimeError(f"Task 054-B stage not complete:{stage}:{proof.get('status')}")
    _validate_content_hash(proof, f"stage:{stage}")
    _validate_declared_dependencies(proof, completed_stages or {})
    validators = {
        "governed_source": _validate_governed_source,
        "strict_matrix": _validate_strict_matrix,
        "v3_tensor": _validate_v3_tensor,
        "production_firewall_sentinel": _validate_production_sentinel,
        "identity_forensic": _validate_identity_forensic,
        "four_gpu_replay": _validate_four_gpu_replay,
    }
    validation = validators[stage](path, proof, tuple(sorted(str(item) for item in expected_candidate_ids)))
    proof["validation_summary"] = validation
    return proof


def task054b_content_hash(payload: Mapping[str, Any]) -> str:
    return _canonical_hash(_semantic_payload(payload))


def _validate_governed_source(path: Path, proof: Mapping[str, Any], _: tuple[str, ...]) -> dict[str, Any]:
    manifest = _validated_manifest(path, proof)
    if manifest.get("generation_complete") is not True:
        raise RuntimeError("governed source generation incomplete")
    datasets = manifest.get("datasets") or {}
    if not TASK054B_REQUIRED_DATASETS.issubset(datasets):
        raise RuntimeError("governed source required dataset missing")
    totals: dict[str, int] = {}
    for dataset_name, record in sorted(datasets.items()):
        if record.get("coverage_validated") is not True or record.get("schema_validated") is not True:
            raise RuntimeError(f"governed source dataset proof incomplete:{dataset_name}")
        if not _is_sha256(str(record.get("contract_hash") or "")):
            raise RuntimeError(f"governed source contract hash invalid:{dataset_name}")
        request_range = record.get("request_range") or {}
        if not request_range.get("start_date") or not request_range.get("end_date"):
            raise RuntimeError(f"governed source request range missing:{dataset_name}")
        records_path = _resolve(path.parent, record.get("records_path"))
        ledger_path = _resolve(path.parent, record.get("coverage_ledger_path"))
        _require_file_hash(records_path, record.get("records_sha256"), f"governed records:{dataset_name}")
        _require_file_hash(ledger_path, record.get("coverage_ledger_sha256"), f"coverage ledger:{dataset_name}")
        actual_count = _count_records(records_path)
        if actual_count != int(record.get("record_count", -1)):
            raise RuntimeError(f"governed source record count mismatch:{dataset_name}")
        ledger = _load_json(ledger_path)
        if ledger.get("dataset") != dataset_name or ledger.get("coverage_complete") is not True:
            raise RuntimeError(f"governed source ledger invalid:{dataset_name}")
        if ledger.get("records_sha256") != record.get("records_sha256"):
            raise RuntimeError(f"governed source ledger lineage mismatch:{dataset_name}")
        totals[dataset_name] = actual_count
    return {"dataset_count": len(datasets), "record_counts": totals}


def _validate_strict_matrix(path: Path, proof: Mapping[str, Any], _: tuple[str, ...]) -> dict[str, Any]:
    manifest = _validated_manifest(path, proof)
    if manifest.get("eligibility_contract_applied") is not True:
        raise RuntimeError("strict matrix eligibility contract not applied")
    if manifest.get("research_holdout_firewall_enabled") is True or manifest.get("research_firewall_ready") is True:
        raise RuntimeError("strict matrix contains unproved firewall readiness")
    arrays = manifest.get("arrays") or manifest.get("partitions") or {}
    if not TASK054B_REQUIRED_MATRIX_ARRAYS.issubset(arrays):
        missing = sorted(TASK054B_REQUIRED_MATRIX_ARRAYS - set(arrays))
        raise RuntimeError(f"strict matrix required arrays missing:{missing}")
    shape = tuple(int(item) for item in manifest.get("shape") or ())
    if len(shape) != 2 or min(shape) <= 0:
        raise RuntimeError("strict matrix shape invalid")
    stock_axis_hash = str(manifest.get("stock_axis_hash") or "")
    date_axis_hash = str(manifest.get("date_axis_hash") or "")
    if not _is_sha256(stock_axis_hash) or not _is_sha256(date_axis_hash):
        raise RuntimeError("strict matrix axis hash invalid")
    observed: dict[str, str] = {}
    for name, record in arrays.items():
        array_path = _resolve(path.parent, record.get("path"))
        _require_file_hash(array_path, record.get("sha256"), f"matrix array:{name}")
        array = np.load(array_path, mmap_mode="r", allow_pickle=False)
        expected_shape = (shape[1],) if name == "research_eligible_date_mask" else shape
        if tuple(array.shape) != expected_shape:
            raise RuntimeError(f"strict matrix array shape mismatch:{name}")
        if record.get("dtype") and str(array.dtype) != str(record.get("dtype")):
            raise RuntimeError(f"strict matrix array dtype mismatch:{name}")
        if name in TASK054B_REQUIRED_MATRIX_ARRAYS and array.dtype != np.bool_:
            raise RuntimeError(f"strict matrix mask dtype mismatch:{name}")
        observed[name] = str(record.get("sha256"))
    signal = np.load(_resolve(path.parent, arrays["signal_candidate_cells"]["path"]), mmap_mode="r")
    common = np.load(_resolve(path.parent, arrays["validation_common_cells"]["path"]), mmap_mode="r")
    target = np.load(_resolve(path.parent, arrays["target_available"]["path"]), mmap_mode="r")
    if np.any(common & ~signal) or np.any(common & ~target):
        raise RuntimeError("strict matrix validation cell implication violated")
    return {"shape": list(shape), "partition_count": len(observed)}


def _validate_v3_tensor(path: Path, proof: Mapping[str, Any], expected: tuple[str, ...]) -> dict[str, Any]:
    manifest = _validated_manifest(path, proof)
    values_path = _resolve(path.parent, manifest.get("values_path"))
    validity_path = _resolve(path.parent, manifest.get("validity_path"))
    _require_file_hash(values_path, manifest.get("values_sha256"), "v3 values")
    _require_file_hash(validity_path, manifest.get("validity_sha256"), "v3 validity")
    values = np.load(values_path, mmap_mode="r", allow_pickle=False)
    validity = np.load(validity_path, mmap_mode="r", allow_pickle=False)
    if values.shape != validity.shape or values.ndim != 3:
        raise RuntimeError("v3 tensor values/validity axis mismatch")
    if values.shape[1] != int(manifest.get("feature_count", values.shape[1])):
        raise RuntimeError("v3 tensor feature count mismatch")
    if values.dtype != np.float32 or validity.dtype != np.bool_:
        raise RuntimeError("v3 tensor dtype mismatch")
    if np.any(values[~validity] != 0):
        raise RuntimeError("v3 tensor invalid cells are not stored as zero")
    for field in (
        "matrix_sha256",
        "freeze_sha256",
        "universe_sha256",
        "feature_manifest_sha256",
        "semantic_source_hash",
        "stock_axis_hash",
        "date_axis_hash",
        "feature_axis_hash",
        "target_contract_hash",
    ):
        if not _is_sha256(str(manifest.get(field) or "")):
            raise RuntimeError(f"v3 tensor lineage hash invalid:{field}")
    content_inputs = {
        "values_sha256": manifest["values_sha256"],
        "validity_sha256": manifest["validity_sha256"],
        "matrix_sha256": manifest["matrix_sha256"],
        "freeze_sha256": manifest["freeze_sha256"],
        "universe_sha256": manifest["universe_sha256"],
        "feature_manifest_sha256": manifest["feature_manifest_sha256"],
        "semantic_source_hash": manifest["semantic_source_hash"],
        "stock_axis_hash": manifest["stock_axis_hash"],
        "date_axis_hash": manifest["date_axis_hash"],
        "feature_axis_hash": manifest["feature_axis_hash"],
        "target_contract_hash": manifest["target_contract_hash"],
    }
    if manifest.get("generation_content_hash") != _canonical_hash(content_inputs):
        raise RuntimeError("v3 tensor generation content hash mismatch")
    candidate_blockers = manifest.get("candidate_blockers") or {}
    if expected and set(candidate_blockers) - set(expected):
        raise RuntimeError("v3 tensor blocker candidate set drift")
    return {"shape": list(values.shape), "candidate_blocker_count": len(candidate_blockers)}


def _validate_production_sentinel(path: Path, proof: Mapping[str, Any], _: tuple[str, ...]) -> dict[str, Any]:
    if proof.get("evidence_scope") != "real_production":
        raise RuntimeError("sentinel evidence_scope must equal real_production")
    if proof.get("plan_builder_id") != "task054b_production_plan_v1":
        raise RuntimeError("sentinel production plan builder mismatch")
    if proof.get("sentinel_status") != "passed" or proof.get("research_firewall_ready") is not True:
        raise RuntimeError("blocked sentinel cannot be wrapped as complete")
    research_end_date = str(proof.get("research_end_date") or "")
    if len(research_end_date) != 8 or not research_end_date.isdigit():
        raise RuntimeError("sentinel research cutoff invalid")
    executions = proof.get("executions") or []
    if not isinstance(executions, list) or len(executions) != 12:
        raise RuntimeError("sentinel requires exact 12 executions")
    by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for execution in executions:
        key = (str(execution.get("mutation_kind")), str(execution.get("path_name")))
        if key in by_key:
            raise RuntimeError(f"duplicate sentinel execution:{key}")
        by_key[key] = execution
        _validate_sentinel_execution(path.parent, execution, research_end_date=research_end_date)
    expected_keys = {(mutation, path_name) for mutation in TASK054B_MUTATIONS for path_name in TASK054B_PATHS}
    if set(by_key) != expected_keys:
        raise RuntimeError("sentinel execution matrix mismatch")
    for path_name in TASK054B_PATHS:
        baseline = by_key[("baseline", path_name)]
        post = by_key[("post_cutoff", path_name)]
        inside = by_key[("inside_cutoff", path_name)]
        baseline_research = baseline.get("research_outputs") or {}
        if set(baseline_research) != TASK054B_RESEARCH_OUTPUT_KEYS:
            raise RuntimeError(f"sentinel research output set invalid:{path_name}")
        if post.get("mutation_applied") is not True or post.get("mutation_cell_count", 0) <= 0:
            raise RuntimeError(f"post-cutoff mutation not applied:{path_name}")
        if post.get("research_outputs") != baseline_research:
            raise RuntimeError(f"post-cutoff research leakage:{path_name}")
        if post.get("research_cache_key") != baseline.get("research_cache_key"):
            raise RuntimeError(f"post-cutoff research cache drift:{path_name}")
        if post.get("diagnostic_sha256") == baseline.get("diagnostic_sha256"):
            raise RuntimeError(f"post-cutoff mutation not observed diagnostically:{path_name}")
        if inside.get("mutation_applied") is not True or inside.get("mutation_cell_count", 0) <= 0:
            raise RuntimeError(f"inside-cutoff mutation not applied:{path_name}")
        if inside.get("research_cache_hit") is not False:
            raise RuntimeError(f"inside-cutoff mutation incorrectly hit cache:{path_name}")
        if inside.get("research_cache_key") == baseline.get("research_cache_key"):
            raise RuntimeError(f"inside-cutoff cache key unchanged:{path_name}")
        changed = sum(
            inside.get("research_outputs", {}).get(name) != baseline_research.get(name)
            for name in TASK054B_RESEARCH_OUTPUT_KEYS
        )
        if changed <= 0:
            raise RuntimeError(f"inside-cutoff research mutation unobserved:{path_name}")
    for mutation in TASK054B_MUTATIONS:
        raw_local = by_key[(mutation, "raw_local")]
        raw_scheduler = by_key[(mutation, "raw_scheduler")]
        matrix_local = by_key[(mutation, "matrix_local")]
        matrix_scheduler = by_key[(mutation, "matrix_scheduler")]
        if raw_local.get("research_outputs") != raw_scheduler.get("research_outputs"):
            raise RuntimeError(f"sentinel raw local/scheduler mismatch:{mutation}")
        if matrix_local.get("research_outputs") != matrix_scheduler.get("research_outputs"):
            raise RuntimeError(f"sentinel matrix local/scheduler mismatch:{mutation}")
        if raw_local.get("research_outputs") != matrix_local.get("research_outputs"):
            raise RuntimeError(f"sentinel raw/matrix mismatch:{mutation}")
    return {"execution_count": 12, "scope": "real_production"}


def _validate_identity_forensic(path: Path, proof: Mapping[str, Any], expected: tuple[str, ...]) -> dict[str, Any]:
    candidates = proof.get("candidates") or []
    if len(candidates) != 20 or len({str(row.get("candidate_id")) for row in candidates}) != 20:
        raise RuntimeError("identity forensic requires 20 unique fixed probes")
    observed = sorted(str(row.get("candidate_id")) for row in candidates)
    if expected and observed != list(expected):
        raise RuntimeError("identity forensic candidate exact set mismatch")
    for row in candidates:
        candidate_id = str(row.get("candidate_id") or "")
        source_path = _resolve(path.parent, row.get("source_factor_record_path"))
        overlay_path = _resolve(path.parent, row.get("normalized_overlay_path"))
        _require_file_hash(source_path, row.get("source_factor_record_sha256"), f"identity source:{candidate_id}")
        _require_file_hash(overlay_path, row.get("normalized_overlay_sha256"), f"identity overlay:{candidate_id}")
        if row.get("formula_identity_preserved") is not True:
            raise RuntimeError(f"formula identity not preserved:{candidate_id}")
        for field in ("syntax_verified", "formula_hash_verified", "factor_id_verified", "normalization_lineage_verified"):
            if row.get(field) is not True:
                raise RuntimeError(f"identity verification failed:{candidate_id}:{field}")
        overlay = _load_json(overlay_path)
        if overlay.get("candidate_id") != candidate_id:
            raise RuntimeError(f"normalized overlay candidate mismatch:{candidate_id}")
        if overlay.get("source_factor_record_sha256") != row.get("source_factor_record_sha256"):
            raise RuntimeError(f"normalized overlay source lineage mismatch:{candidate_id}")
    return {"candidate_count": 20, "formula_identity_verified": True}


def _validate_four_gpu_replay(path: Path, proof: Mapping[str, Any], expected: tuple[str, ...]) -> dict[str, Any]:
    if len(expected) != 20:
        raise RuntimeError("four-GPU replay expected candidate set must contain 20 IDs")
    evidence_paths = [_resolve(path.parent, item) for item in proof.get("replay_evidence_paths") or []]
    replay = validate_task054_replay_evidence(
        evidence_paths,
        expected,
        require_uncached_materialization=bool(proof.get("require_uncached_materialization", True)),
        expected_bundle_hash=proof.get("replay_bundle_hash"),
    )
    if proof.get("replay_truth_hash") != replay["replay_truth_hash"]:
        raise RuntimeError("four-GPU replay truth hash mismatch")
    state_paths = proof.get("scheduler_state_paths") or {}
    if len(state_paths) != 4:
        raise RuntimeError("four-GPU scheduler state set incomplete")
    for shard in replay["shards"]:
        job_id = str(shard.get("job_id") or "")
        state_path = _resolve(path.parent, state_paths.get(job_id))
        state = _load_json(state_path)
        _validate_scheduler_state(state, shard, expected_job_id=job_id)
    if proof.get("primary_uncached_hash") != proof.get("sibling_uncached_hash"):
        raise RuntimeError("uncached sibling replay hash mismatch")
    if int(proof.get("resume_hit_count", -1)) != 4:
        raise RuntimeError("immutable replay resume is not 4/4")
    return replay


def _validate_sentinel_execution(root: Path, execution: Mapping[str, Any], *, research_end_date: str) -> None:
    if execution.get("evidence_scope") != "real_production" or execution.get("status") != "succeeded":
        raise RuntimeError("synthetic or failed sentinel execution rejected")
    invocation_id = str(execution.get("invocation_id") or "")
    if not invocation_id:
        raise RuntimeError("sentinel invocation ID missing")
    source_generation = execution.get("source_generation") or {}
    expected_kind = "governed_freeze_rebuild" if str(execution.get("path_name") or "").startswith("raw_") else "published_strict_generation"
    if source_generation.get("generation_kind") != expected_kind or not source_generation.get("artifact_id"):
        raise RuntimeError("sentinel source generation contract invalid")
    source_manifest_path = _resolve(root, source_generation.get("manifest_path"))
    _require_file_hash(source_manifest_path, source_generation.get("manifest_sha256"), "sentinel source generation manifest")
    receipts = execution.get("component_receipts") or []
    components = {str(row.get("component")) for row in receipts}
    if components != TASK054B_REQUIRED_COMPONENTS:
        raise RuntimeError("sentinel production component receipt set incomplete")
    for receipt in receipts:
        if receipt.get("receipt_type") != "production_component_receipt_v1":
            raise RuntimeError("source-hash-only component evidence rejected")
        if receipt.get("status") != "completed" or receipt.get("invocation_id") != invocation_id:
            raise RuntimeError("component receipt invocation mismatch")
        entrypoint = str(receipt.get("entrypoint") or "")
        if not entrypoint or entrypoint.rsplit(".", 1)[-1].startswith("_"):
            raise RuntimeError("component receipt does not identify a public entrypoint")
        if not _is_sha256(str(receipt.get("source_hash") or "")):
            raise RuntimeError("component receipt source hash invalid")
        if invocation_id not in (receipt.get("invocation_chain") or []):
            raise RuntimeError("component receipt invocation chain incomplete")
        if not receipt.get("started_at") or not receipt.get("finished_at"):
            raise RuntimeError("component receipt timing missing")
        inputs = receipt.get("input_artifacts") or []
        outputs = receipt.get("outputs") or []
        if not inputs or not outputs:
            raise RuntimeError("component receipt input/output lineage missing")
        for item in inputs:
            if not item.get("artifact_id") or not _is_sha256(str(item.get("sha256") or "")):
                raise RuntimeError("component receipt input lineage invalid")
        for item in outputs:
            output_path = _resolve(root, item.get("path"))
            _require_file_hash(output_path, item.get("sha256"), "component receipt output")
        _validate_embedded_hash(receipt, "receipt_hash", "component receipt")
    ledger_path = _resolve(root, execution.get("read_ledger_path"))
    _require_file_hash(ledger_path, execution.get("read_ledger_sha256"), "sentinel read ledger")
    ledger = [_load_json_line(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not ledger:
        raise RuntimeError("sentinel audited read ledger empty")
    for row in ledger:
        if row.get("emitter") != "audited_read_broker" or row.get("invocation_id") != invocation_id:
            raise RuntimeError("hand-written read ledger rejected")
        if not row.get("principal") or not row.get("component") or not row.get("artifact_id"):
            raise RuntimeError("read ledger identity incomplete")
        if not _is_sha256(str(row.get("artifact_sha256") or "")):
            raise RuntimeError("read ledger artifact hash invalid")
        if not (row.get("date_range") or {}).get("start") or not (row.get("date_range") or {}).get("end"):
            raise RuntimeError("read ledger date range incomplete")
        if row.get("policy_decision") not in {"allowed", "denied"}:
            raise RuntimeError("read ledger policy decision invalid")
        if row.get("principal") == "research" and row.get("policy_decision") != "allowed":
            raise RuntimeError("research principal attempted out-of-bounds read")
        _validate_embedded_hash(row, "broker_receipt_hash", "read broker receipt")
    broker_receipt_path = _resolve(root, execution.get("read_broker_receipt_path"))
    _require_file_hash(broker_receipt_path, execution.get("read_broker_receipt_sha256"), "read broker attestation")
    broker_receipt = _load_json(broker_receipt_path)
    if broker_receipt.get("receipt_type") != "audited_read_broker_receipt_v1":
        raise RuntimeError("hand-written read ledger rejected")
    if broker_receipt.get("invocation_id") != invocation_id or broker_receipt.get("ledger_sha256") != execution.get("read_ledger_sha256"):
        raise RuntimeError("hand-written read ledger rejected: broker binding mismatch")
    if int(broker_receipt.get("row_count", -1)) != len(ledger):
        raise RuntimeError("read broker ledger row count mismatch")
    if not _is_sha256(str(broker_receipt.get("broker_source_hash") or "")):
        raise RuntimeError("read broker source hash invalid")
    _validate_embedded_hash(broker_receipt, "receipt_hash", "read broker attestation")
    mutation_kind = str(execution.get("mutation_kind") or "")
    if mutation_kind != "baseline":
        mutation_manifest_path = _resolve(root, execution.get("mutation_manifest_path"))
        _require_file_hash(mutation_manifest_path, execution.get("mutation_manifest_sha256"), "mutation manifest")
        mutation_manifest = _load_json(mutation_manifest_path)
        _validate_mutation_manifest(root, mutation_manifest, mutation_kind=mutation_kind, research_end_date=research_end_date)
    if str(execution.get("path_name") or "").endswith("scheduler"):
        scheduler = execution.get("scheduler_evidence") or {}
        state_path = _resolve(root, scheduler.get("state_path"))
        _require_file_hash(state_path, scheduler.get("state_sha256"), "scheduler state")
        state = _load_json(state_path)
        _validate_scheduler_state(state, scheduler, expected_job_id=str(scheduler.get("job_id") or ""))
    for field in ("research_cache_key", "diagnostic_sha256"):
        if not _is_sha256(str(execution.get(field) or "")):
            raise RuntimeError(f"sentinel execution hash invalid:{field}")
    research_outputs = execution.get("research_outputs") or {}
    if set(research_outputs) != TASK054B_RESEARCH_OUTPUT_KEYS:
        raise RuntimeError("sentinel research outputs incomplete")
    if any(not _is_sha256(str(value)) for value in research_outputs.values()):
        raise RuntimeError("sentinel research output hash invalid")


def _validate_mutation_manifest(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    mutation_kind: str,
    research_end_date: str,
) -> None:
    if manifest.get("mutation_kind") != mutation_kind or manifest.get("pre_registered") is not True:
        raise RuntimeError("sentinel mutation was not pre-registered")
    if manifest.get("target_or_outcome_used") is not False:
        raise RuntimeError("sentinel mutation used target or outcome information")
    if not _is_sha256(str(manifest.get("probe_formula_hash") or "")):
        raise RuntimeError("sentinel mutation probe formula hash invalid")
    if manifest.get("source_generation_id") == manifest.get("mutated_generation_id"):
        raise RuntimeError("sentinel mutation did not create a new generation")
    original_path = _resolve(root, manifest.get("original_partition_path"))
    mutated_path = _resolve(root, manifest.get("mutated_partition_path"))
    _require_file_hash(original_path, manifest.get("original_partition_sha256"), "mutation original partition")
    _require_file_hash(mutated_path, manifest.get("mutated_partition_sha256"), "mutation generated partition")
    if manifest.get("original_partition_sha256") == manifest.get("mutated_partition_sha256"):
        raise RuntimeError("sentinel mutation partition unchanged")
    mutation_date = str((manifest.get("cell") or {}).get("date") or "")
    if mutation_kind == "post_cutoff" and mutation_date <= research_end_date:
        raise RuntimeError("post-cutoff mutation is not after research endpoint")
    if mutation_kind == "inside_cutoff" and mutation_date > research_end_date:
        raise RuntimeError("inside-cutoff mutation is outside research period")
    _validate_embedded_hash(manifest, "content_hash", "mutation manifest")


def _validate_scheduler_state(state: Mapping[str, Any], evidence: Mapping[str, Any], *, expected_job_id: str) -> None:
    if state.get("job_id") != expected_job_id or evidence.get("job_id") != expected_job_id:
        raise RuntimeError("scheduler job ID/state mismatch")
    for field in ("run_id", "attempt", "exit_code", "heartbeat_count"):
        if state.get(field) != evidence.get(field):
            raise RuntimeError(f"scheduler state mismatch:{field}")
    if state.get("status") != "succeeded" or int(state.get("exit_code", -1)) != 0:
        raise RuntimeError("scheduler state not successful")
    if int(state.get("heartbeat_count", 0)) <= 0:
        raise RuntimeError("scheduler heartbeat missing")
    if not state.get("device_info"):
        raise RuntimeError("scheduler device information missing")


def _validated_manifest(proof_path: Path, proof: Mapping[str, Any]) -> dict[str, Any]:
    manifest_path = _resolve(proof_path.parent, proof.get("artifact_manifest_path"))
    _require_file_hash(manifest_path, proof.get("artifact_manifest_sha256"), "stage artifact manifest")
    result = validate_artifact(manifest_path, strict=True)
    if not result.valid:
        codes = ",".join(issue.code for issue in result.issues)
        raise RuntimeError(f"stage artifact schema invalid:{proof.get('stage')}:{codes}")
    return _load_json(manifest_path)


def _validate_declared_dependencies(proof: Mapping[str, Any], completed: Mapping[str, Mapping[str, Any]]) -> None:
    lineage = proof.get("lineage") or {}
    for dependency, payload in completed.items():
        if lineage.get(dependency) != payload.get("content_hash"):
            raise RuntimeError(f"Task 054-B upstream lineage mismatch:{proof.get('stage')}:{dependency}")


def _validate_content_hash(payload: Mapping[str, Any], label: str) -> None:
    if payload.get("content_hash") != task054b_content_hash(payload):
        raise RuntimeError(f"Task 054-B content hash mismatch:{label}")


def _validate_embedded_hash(payload: Mapping[str, Any], field: str, label: str) -> None:
    semantic = {key: value for key, value in payload.items() if key != field}
    if payload.get(field) != _canonical_hash(semantic):
        raise RuntimeError(f"{label} hash mismatch")


def _semantic_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"content_hash", "created_at", "artifact_metadata", "producer", "schema_version"}
    }


def _resolve(root: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else root / path


def _require_file_hash(path: Path, expected: Any, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"{label} missing:{path}")
    if not _is_sha256(str(expected or "")) or _sha256_file(path) != expected:
        raise RuntimeError(f"{label} SHA mismatch")


def _count_records(path: Path) -> int:
    if path.suffix == ".jsonl":
        return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())
    payload = _load_json(path)
    if isinstance(payload, list):
        return len(payload)
    records = payload.get("records") if isinstance(payload, dict) else None
    if isinstance(records, list):
        return len(records)
    raise RuntimeError(f"unsupported governed records container:{path}")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required:{path}")
    return payload


def _load_json_line(line: str) -> dict[str, Any]:
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise RuntimeError("JSONL object required")
    return payload


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

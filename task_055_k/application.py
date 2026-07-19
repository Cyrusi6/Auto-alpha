from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from task_055_h.io import atomic_json, canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from task_055_j.application import (
    _production_context,
    apply_synthetic_test_only,
    validate_native_application,
    validate_native_causal_replay,
)

from .contracts import APPLICATION_STAGES


APPLICATION_SCHEMA = "task055k_staged_response_application_v1"
STAGE_JOURNAL_SCHEMA = "task055k_application_stage_journal_v1"


class Task055KApplicationError(RuntimeError):
    pass


class Task055KApplicationCrash(RuntimeError):
    pass


def production_context_from_parent(parent: Mapping[str, Any]) -> dict[str, Any]:
    return _production_context(
        {
            "runtime_authority": parent["runtime"],
            "governed_root": parent["governed_root"],
        }
    )


def apply_staged_synthetic_response(
    *,
    accepted: Mapping[str, Any],
    context: Mapping[str, Any],
    output_root: str | Path,
    crash_after_stage: str | None = None,
    crash_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    scoped_context = dict(context) | {
        "evidence_scope": "synthetic_rehearsal_only",
        "sentinel_timeout_seconds": int(context.get("sentinel_timeout_seconds", 1800)),
    }
    return _apply_staged(
        accepted=accepted,
        context=scoped_context,
        output_root=Path(output_root),
        evidence_scope="synthetic_rehearsal_only",
        crash_after_stage=crash_after_stage,
        crash_hook=crash_hook,
    )


def validate_staged_application(path: str | Path, *, authority_root: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=APPLICATION_SCHEMA, manifest_name="response_application.json")
    if payload.get("status") != "applied" or payload.get("evidence_scope") not in {
        "synthetic_rehearsal_only",
        "real_production",
    }:
        raise Task055KApplicationError("task055k_application_status_or_scope_invalid")
    if payload.get("evidence_scope") == "synthetic_rehearsal_only" and payload.get("production_seal_eligible") is not False:
        raise Task055KApplicationError("task055k_synthetic_application_scope_boundary_invalid")
    journal_path = Path(authority_root) / str(payload["stage_journal_relative_path"])
    journal = validate_stage_journal(journal_path)
    if journal["application_spec_hash"] != payload.get("application_spec_hash"):
        raise Task055KApplicationError("task055k_application_journal_spec_mismatch")
    if journal["stage_root"] != payload.get("stage_root"):
        raise Task055KApplicationError("task055k_application_journal_root_mismatch")
    j_application = validate_native_application(
        Path(authority_root) / str(payload["task055j_application_relative_path"]),
        authority_root=authority_root,
    )
    if j_application["content_hash"] != payload.get("task055j_application_content_hash"):
        raise Task055KApplicationError("task055k_application_parent_output_mismatch")
    replay = validate_native_causal_replay(
        Path(authority_root) / str(payload["native_replay_relative_path"])
    )
    expected_roots = {
        "run_rows_root": replay["run_rows_root"],
        "held_mark_root": replay["held_mark_root"],
        "net_frontier_root": replay["net_frontier_root"],
        "all_in_frontier_root": replay["all_in_frontier_root"],
        "frontier_union_root": replay["frontier_union_root"],
    }
    if payload.get("replay_roots") != expected_roots:
        raise Task055KApplicationError("task055k_application_replay_roots_mismatch")
    return payload | {"stage_journal": journal, "task055j_application": j_application, "replay": replay}


def validate_stage_journal(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    unsigned = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("schema_version") != STAGE_JOURNAL_SCHEMA or canonical_hash(unsigned) != payload.get("content_hash"):
        raise Task055KApplicationError("task055k_stage_journal_hash_invalid")
    stages = payload.get("stages") or {}
    if set(stages) != set(APPLICATION_STAGES):
        raise Task055KApplicationError("task055k_stage_journal_stage_set_invalid")
    previous = payload["application_spec_hash"]
    for stage_name in APPLICATION_STAGES:
        stage = stages[stage_name]
        if stage.get("status") != "completed" or stage.get("input_root") != previous:
            raise Task055KApplicationError(f"task055k_stage_journal_chain_invalid:{stage_name}")
        if not _hash64(stage.get("output_content_hash")) or not stage.get("validator"):
            raise Task055KApplicationError(f"task055k_stage_journal_output_invalid:{stage_name}")
        previous = stage["output_content_hash"]
    if payload.get("final_stage_root") != previous:
        raise Task055KApplicationError("task055k_stage_journal_final_root_invalid")
    return payload


def _apply_staged(
    *,
    accepted: Mapping[str, Any],
    context: Mapping[str, Any],
    output_root: Path,
    evidence_scope: str,
    crash_after_stage: str | None,
    crash_hook: Callable[[str], None] | None,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / "application.lock"
    lock_path.touch(exist_ok=True)
    initial_lock = _lock_identity(lock_path)
    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _lock_identity(lock_path) != initial_lock or os.fstat(lock.fileno()).st_ino != initial_lock[1]:
            raise Task055KApplicationError("task055k_application_lock_inode_replaced")
        spec_hash = canonical_hash(
            [
                accepted["acceptance"]["content_hash"],
                context["context_root"],
                accepted["transport_receipt"]["content_hash"],
                accepted["acceptance"]["cache_sha256"],
                evidence_scope,
            ]
        )
        stage_root = output_root / "stages" / f"application_{spec_hash[:24]}"
        stage_root.mkdir(parents=True, exist_ok=True)
        (stage_root / "application.lock").touch(exist_ok=True)
        journal_path = stage_root / "stage_journal.json"
        existing_final = _find_application(output_root, spec_hash)
        if existing_final is not None:
            return validate_staged_application(existing_final, authority_root=output_root.parent)
        native_root = output_root.parent / f"native_task055j_{spec_hash[:24]}_{output_root.name}"
        native_application = apply_synthetic_test_only(
            accepted=accepted,
            context=context,
            output_root=native_root,
        )
        native = validate_native_application(
            native_application["manifest_path"],
            authority_root=native_root.parent,
        )
        stage_descriptors = _stage_descriptors(native, records=accepted["records"])
        journal = _load_partial_journal(journal_path, spec_hash=spec_hash, stage_root=stage_root)
        previous = spec_hash
        for name in APPLICATION_STAGES:
            descriptor = stage_descriptors[name]
            prior = (journal.get("stages") or {}).get(name)
            if prior is not None:
                if prior.get("input_root") != previous or prior.get("output_content_hash") != descriptor["output_content_hash"]:
                    raise Task055KApplicationError(f"task055k_application_resume_stage_drift:{name}")
                _validate_stage_descriptor(name, descriptor, native)
            else:
                _validate_stage_descriptor(name, descriptor, native)
                journal.setdefault("stages", {})[name] = {
                    "status": "completed",
                    "input_root": previous,
                    **descriptor,
                }
                _write_partial_journal(journal_path, journal)
            previous = descriptor["output_content_hash"]
            if _lock_identity(lock_path) != initial_lock or os.fstat(lock.fileno()).st_ino != initial_lock[1]:
                raise Task055KApplicationError("task055k_application_lock_inode_replaced")
            if crash_hook is not None:
                crash_hook(name)
            if crash_after_stage == name:
                raise Task055KApplicationCrash(f"task055k_crash_after_stage:{name}")
        completed = {
            "schema_version": STAGE_JOURNAL_SCHEMA,
            "status": "completed",
            "application_spec_hash": spec_hash,
            "stage_root": stage_root.relative_to(output_root.parent).as_posix(),
            "stages": {name: journal["stages"][name] for name in APPLICATION_STAGES},
            "final_stage_root": previous,
        }
        completed["content_hash"] = canonical_hash(completed)
        atomic_json(journal_path, completed)
        replay = native["replay"]
        semantic = {
            "schema_version": APPLICATION_SCHEMA,
            "status": "applied",
            "evidence_scope": evidence_scope,
            "production_seal_eligible": evidence_scope == "real_production",
            "application_spec_hash": spec_hash,
            "stage_root": completed["stage_root"],
            "stage_journal_relative_path": journal_path.relative_to(output_root.parent).as_posix(),
            "stage_journal_content_hash": completed["content_hash"],
            "task055j_application_relative_path": Path(native["manifest_path"]).relative_to(output_root.parent).as_posix(),
            "task055j_application_content_hash": native["content_hash"],
            "native_replay_relative_path": Path(replay["manifest_path"]).relative_to(output_root.parent).as_posix(),
            "replay_roots": {
                "run_rows_root": replay["run_rows_root"],
                "held_mark_root": replay["held_mark_root"],
                "net_frontier_root": replay["net_frontier_root"],
                "all_in_frontier_root": replay["all_in_frontier_root"],
                "frontier_union_root": replay["frontier_union_root"],
            },
            "terminal_pair_count": native["terminal_pair_count"],
            "terminal_counts": native["terminal_counts"],
            "candidate_reselection_allowed": False,
            "network_executed": False,
        }
        result = publish_generation(
            output_root,
            prefix="task055k_response_application",
            manifest_name="response_application.json",
            semantic=semantic,
        )
        return validate_staged_application(result["manifest_path"], authority_root=output_root.parent)


def _stage_descriptors(native: Mapping[str, Any], *, records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    outputs = native["stage_outputs"]
    replay = native["replay"]
    no_op = canonical_hash([native["content_hash"], "not_applicable", len(records)])
    return {
        "response_acceptance": _descriptor(native["canary_acceptance_content_hash"], "validate_canary_acceptance"),
        "raw_repair": _descriptor(outputs.get("raw_repair", no_op), "validate_raw_repair_or_not_applicable"),
        "truth_successor": _descriptor(outputs["truth"], "validate_truth_v2"),
        "freeze": _descriptor(outputs.get("freeze", no_op), "validate_task052_governed_freeze_or_parent"),
        "strict_matrix": _descriptor(outputs.get("matrix", no_op), "validate_strict_matrix_generation_or_parent"),
        "v3_tensor": _descriptor(outputs.get("tensor", no_op), "validate_v3_tensor_generation_or_parent"),
        "exact20_materialization": _descriptor(outputs.get("exact20_materialization", no_op), "validate_exact20_materializations_or_parent"),
        "firewall_sentinel": _descriptor(outputs.get("firewall_sentinel", no_op), "validate_task054b_sentinel_or_parent"),
        "valuation": _descriptor(replay["valuation_projection_content_hash"], "independent_rebuild_valuation_surface"),
        "net_replay": _descriptor(canonical_hash([replay["run_rows_root"], replay["net_frontier_root"]]), "independent_trace_net_commission"),
        "all_in_replay": _descriptor(canonical_hash([replay["held_mark_root"], replay["all_in_frontier_root"]]), "independent_trace_all_in_commission"),
        "final_publication": _descriptor(native["content_hash"], "validate_native_application"),
    }


def _descriptor(content_hash: str, validator: str) -> dict[str, str]:
    if not _hash64(content_hash):
        raise Task055KApplicationError(f"task055k_stage_output_hash_invalid:{validator}")
    return {"output_content_hash": content_hash, "validator": validator, "terminal": "success"}


def _validate_stage_descriptor(name: str, descriptor: Mapping[str, str], native: Mapping[str, Any]) -> None:
    if descriptor.get("terminal") != "success" or not _hash64(descriptor.get("output_content_hash")):
        raise Task055KApplicationError(f"task055k_stage_native_validation_failed:{name}")
    if name == "final_publication" and descriptor["output_content_hash"] != native["content_hash"]:
        raise Task055KApplicationError("task055k_final_publication_hash_invalid")


def _load_partial_journal(path: Path, *, spec_hash: str, stage_root: Path) -> dict[str, Any]:
    if not path.is_file():
        payload = {
            "schema_version": STAGE_JOURNAL_SCHEMA,
            "status": "running",
            "application_spec_hash": spec_hash,
            "stage_root": stage_root.name,
            "stages": {},
        }
        _write_partial_journal(path, payload)
        return payload
    payload = read_json(path)
    if payload.get("application_spec_hash") != spec_hash:
        raise Task055KApplicationError("task055k_partial_stage_journal_spec_drift")
    if payload.get("status") == "completed":
        validate_stage_journal(path)
    return payload


def _write_partial_journal(path: Path, payload: Mapping[str, Any]) -> None:
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    atomic_json(path, semantic | {"content_hash": canonical_hash(semantic)})


def _find_application(root: Path, spec_hash: str) -> Path | None:
    matches = []
    for path in sorted((root / "generations").glob("*/response_application.json")):
        if read_json(path).get("application_spec_hash") == spec_hash:
            matches.append(path)
    if len(matches) > 1:
        raise Task055KApplicationError("task055k_duplicate_application_generation")
    return matches[0] if matches else None


def _lock_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise Task055KApplicationError("task055k_application_lock_invalid")
    return metadata.st_dev, metadata.st_ino


def _hash64(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)

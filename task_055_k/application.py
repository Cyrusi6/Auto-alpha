from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from task_055_h.io import canonical_hash, read_json, validate_generation
from task_055_j.application import _production_context

from .application_components import production_stage_definitions
from .broker import AcceptedResponse
from .contracts import (
    APPLICATION_JOURNAL_SCHEMA,
    APPLICATION_SCHEMA,
    APPLICATION_STAGES,
)
from .stage_machine import ApplicationStageMachine, Task055KStageMachineError


class Task055KApplicationError(RuntimeError):
    pass


def production_context_from_parent(parent: Mapping[str, Any]) -> dict[str, Any]:
    context = _production_context(
        {
            "runtime_authority": parent["runtime"],
            "governed_root": parent["governed_root"],
        }
    )
    context["sentinel_timeout_seconds"] = int(context.get("sentinel_timeout_seconds", 1800))
    parent_truth = read_json(context["truth_manifest"])
    expected_truth_count = int(parent_truth.get("record_count") or 0)
    if expected_truth_count <= 0:
        raise Task055KApplicationError("task055k_parent_truth_record_count_invalid")
    context["expected_truth_record_count"] = expected_truth_count
    return context


def application_spec_hash(
    *, accepted: AcceptedResponse, context: Mapping[str, Any], evidence_scope: str
) -> str:
    if evidence_scope != accepted.scope:
        raise Task055KApplicationError("task055k_application_acceptance_scope_mismatch")
    if evidence_scope not in {"real_production", "synthetic_rehearsal_only"}:
        raise Task055KApplicationError("task055k_application_scope_invalid")
    definitions = production_stage_definitions()
    return canonical_hash(
        {
            "contract": "task055kr_native_stage_machine_v2",
            "acceptance_content_hash": accepted.acceptance["content_hash"],
            "reservation_content_hash": accepted.reservation["content_hash"],
            "receipt_content_hash": accepted.receipt["content_hash"],
            "cache_sha256": accepted.acceptance["cache_sha256"],
            "context_root": context["context_root"],
            "runtime_semantic_source_hash": runtime_semantic_source_hash(),
            "evidence_scope": evidence_scope,
            "stages": [
                {"name": definition.name, "validator_fqn": definition.validator_fqn}
                for definition in definitions
            ],
        }
    )


def apply_accepted_response(
    *,
    accepted: AcceptedResponse,
    context: Mapping[str, Any],
    output_root: str | Path,
    evidence_scope: str | None = None,
    crash_point: str | None = None,
) -> dict[str, Any]:
    scope = evidence_scope or accepted.scope
    machine = _machine(
        accepted=accepted,
        context=context,
        output_root=output_root,
        evidence_scope=scope,
    )
    return machine.run(crash_point=crash_point)


def validate_staged_application(
    path: str | Path,
    *,
    accepted: AcceptedResponse,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = Path(path).resolve()
    payload = validate_generation(
        manifest,
        schema=APPLICATION_SCHEMA,
        manifest_name="response_application.json",
    )
    root = _application_root(manifest)
    machine = _machine(
        accepted=accepted,
        context=context,
        output_root=root,
        evidence_scope=str(payload.get("evidence_scope") or ""),
    )
    validated = machine.validate_application(manifest)
    if tuple(row["stage"] for row in validated["stages"]) != APPLICATION_STAGES:
        raise Task055KApplicationError("task055k_application_stage_order_invalid")
    return validated


def validate_stage_journal(
    path: str | Path,
    *,
    expected_application_spec_hash: str | None = None,
) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=APPLICATION_JOURNAL_SCHEMA,
        manifest_name="stage_journal.json",
    )
    rows = payload.get("stages") or []
    if payload.get("status") != "completed" or len(rows) != len(APPLICATION_STAGES):
        raise Task055KApplicationError("task055k_stage_journal_incomplete")
    if [row.get("stage") for row in rows] != list(APPLICATION_STAGES):
        raise Task055KApplicationError("task055k_stage_journal_stage_order_invalid")
    if expected_application_spec_hash is not None and payload.get(
        "application_spec_hash"
    ) != expected_application_spec_hash:
        raise Task055KApplicationError("task055k_stage_journal_spec_invalid")
    previous = payload["application_spec_hash"]
    for ordinal, row in enumerate(rows, start=1):
        if (
            row.get("ordinal") != ordinal
            or row.get("input_root") != previous
            or not _hash64(row.get("output_content_hash"))
            or not str(row.get("validator_fqn") or "").startswith(
                "task_055_k.application_components."
            )
        ):
            raise Task055KApplicationError(
                f"task055k_stage_journal_cross_lineage_invalid:{ordinal}"
            )
        previous = row["output_content_hash"]
    if payload.get("final_stage_root") != previous:
        raise Task055KApplicationError("task055k_stage_journal_final_root_invalid")
    counts = payload.get("stage_execution_counts") or {}
    if set(counts) != set(APPLICATION_STAGES) or any(
        not isinstance(counts[name], int) or counts[name] != 1 for name in APPLICATION_STAGES
    ):
        raise Task055KApplicationError("task055k_stage_journal_execution_counts_invalid")
    return payload


def _machine(
    *,
    accepted: AcceptedResponse,
    context: Mapping[str, Any],
    output_root: str | Path,
    evidence_scope: str,
) -> ApplicationStageMachine:
    enriched = dict(context)
    source_hash = runtime_semantic_source_hash()
    if enriched.get("runtime_semantic_source_hash") not in {None, source_hash}:
        raise Task055KApplicationError("task055k_application_runtime_source_hash_drift")
    enriched["runtime_semantic_source_hash"] = source_hash
    return ApplicationStageMachine(
        application_root=output_root,
        application_spec_hash=application_spec_hash(
            accepted=accepted,
            context=enriched,
            evidence_scope=evidence_scope,
        ),
        evidence_scope=evidence_scope,
        accepted=accepted,
        context=enriched,
        stages=production_stage_definitions(),
    )


def _application_root(manifest: Path) -> Path:
    if manifest.name != "response_application.json" or manifest.parent.parent.name != "generations":
        raise Task055KApplicationError("task055k_application_manifest_location_invalid")
    return manifest.parents[2]


def _hash64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def runtime_semantic_source_hash() -> str:
    repository = Path(__file__).resolve().parents[1]
    rows = []
    for path in sorted(repository.rglob("*.py")):
        relative = path.relative_to(repository).as_posix()
        if relative.startswith(("tests/", "dev_tools/", ".git/")) or "__pycache__" in path.parts:
            continue
        rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    for relative in ("pyproject.toml", "uv.lock"):
        path = repository / relative
        if path.is_file():
            rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return canonical_hash(rows)


__all__ = [
    "APPLICATION_STAGES",
    "Task055KApplicationError",
    "Task055KStageMachineError",
    "application_spec_hash",
    "apply_accepted_response",
    "production_context_from_parent",
    "runtime_semantic_source_hash",
    "validate_stage_journal",
    "validate_staged_application",
]

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_055_h.io import canonical_hash, read_json, sha256_file

from .authority import (
    publish_candidate_authority,
    publish_candidate_checkpoint,
    publish_final_candidate_seal,
    publish_historical_supersession,
    publish_parent_verification,
    validate_final_candidate_seal,
    validate_task055j_parent,
)
from .broker import broker_contract_hash
from .contracts import (
    APPLICATION_STAGES,
    BLOCKED_STATUS,
    CERTIFICATION_BLOCKERS,
    ENGINEERING_WARNINGS,
    FINAL_REPORT_SCHEMA,
    FINAL_VERIFICATION_SCHEMA,
    READY_STATUS,
    SCRUBBED_SCHEMA,
    TASK055K_AUTHORITY_RELATIVE_ROOT,
    TASK055K_RELATIVE_ROOT,
)
from .immutable import write_immutable_generation
from .rehearsal import independently_verify_rehearsal, validate_rehearsal
from .source_tree import publish_git_index_source_seal, validate_git_index_source_seal


class Task055KRunError(RuntimeError):
    pass


THREAT_MODEL = {
    "in_scope": [
        "trusted_operator_and_reviewed_repository_code",
        "crash_concurrency_duplicate_start_cache_corruption_operator_error",
        "known_legacy_writers_and_authority_roots",
    ],
    "out_of_scope": [
        "malicious_root",
        "arbitrary_malicious_same_uid_code",
        "simultaneous_full_server_and_remote_rollback",
    ],
    "high_assurance_limitation": "external_worm_or_monotonic_counter_unavailable",
    "functional_single_read_canary_blocked_by_limitation": False,
}


def prepare_runtime_foundation(
    *, repository_root: str | Path, parent_task055j_final_seal: str | Path
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    _require_clean(repository)
    implementation_commit = _git(repository, "rev-parse", "HEAD")
    parent = validate_task055j_parent(
        final_seal_path=parent_task055j_final_seal,
        repository_root=repository,
    )
    governed = Path(parent["governed_root"])
    output = governed / TASK055K_RELATIVE_ROOT
    authority_root = governed / TASK055K_AUTHORITY_RELATIVE_ROOT
    output.mkdir(parents=True, exist_ok=True)
    authority_root.mkdir(parents=True, exist_ok=True)
    parent_verification = publish_parent_verification(
        verified_parent=parent,
        output_root=output / "parent_verification",
    )
    source = publish_git_index_source_seal(
        repository_root=repository,
        output_root=output / "source_seal",
        implementation_commit=implementation_commit,
    )
    source = validate_git_index_source_seal(
        source["manifest_path"],
        repository_root=repository,
        require_clean=True,
        allowed_evidence_descendant=False,
    )
    supersession = publish_historical_supersession(
        output_root=output / "historical_supersession"
    )
    authority = publish_candidate_authority(
        verified_parent=parent,
        parent_verification=parent_verification,
        source_seal=source,
        implementation_commit=implementation_commit,
        output_root=authority_root / "candidate_authority",
    )
    return {
        "repository": str(repository),
        "implementation_commit": implementation_commit,
        "parent": parent,
        "governed": str(governed),
        "output": str(output),
        "authority_root": str(authority_root),
        "parent_verification": parent_verification,
        "source_seal": source,
        "historical_supersession": supersession,
        "candidate_authority": authority,
    }


def finalize_offline_evidence(
    *,
    repository_root: str | Path,
    parent_task055j_final_seal: str | Path,
    rehearsal_manifest: str | Path,
) -> dict[str, Any]:
    foundation = prepare_runtime_foundation(
        repository_root=repository_root,
        parent_task055j_final_seal=parent_task055j_final_seal,
    )
    repository = Path(foundation["repository"])
    output = Path(foundation["output"])
    authority_root = Path(foundation["authority_root"])
    rehearsal = validate_rehearsal(rehearsal_manifest)
    if rehearsal.get("candidate_authority_content_hash") != foundation[
        "candidate_authority"
    ]["content_hash"]:
        raise Task055KRunError("task055k_rehearsal_candidate_authority_mismatch")
    rehearsal_semantic = independently_verify_rehearsal(rehearsal["manifest_path"])
    rehearsal_verification = write_immutable_generation(
        output / "rehearsal_verification",
        prefix="task055kr_rehearsal_verification",
        manifest_name="rehearsal_verification.json",
        semantic={key: value for key, value in rehearsal_semantic.items() if key != "content_hash"},
    )
    threat = write_immutable_generation(
        output / "threat_model",
        prefix="task055kr_threat_model",
        manifest_name="threat_model.json",
        semantic={
            "schema_version": "task055kr_single_read_threat_model_v2",
            "status": "documented",
            **THREAT_MODEL,
        },
    )
    blockers: list[str] = []
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    application_role_roots = _application_role_roots(rehearsal)
    offline_counters = _offline_counters(rehearsal)
    report = write_immutable_generation(
        output / "final",
        prefix="task055kr_report",
        manifest_name="task055kr_report.json",
        semantic={
            "schema_version": FINAL_REPORT_SCHEMA,
            "status": status,
            "implementation_commit": foundation["implementation_commit"],
            "parent_task055j_final_seal_hash": foundation["parent"][
                "parent_final_execution_seal_content_hash"
            ],
            "parent_verification_content_hash": foundation["parent_verification"][
                "content_hash"
            ],
            "source_seal_content_hash": foundation["source_seal"]["content_hash"],
            "source_root": foundation["source_seal"]["source_root"],
            "historical_supersession_content_hash": foundation[
                "historical_supersession"
            ]["content_hash"],
            "candidate_authority_content_hash": foundation["candidate_authority"][
                "content_hash"
            ],
            "rehearsal_content_hash": rehearsal["content_hash"],
            "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
            "threat_model_content_hash": threat["content_hash"],
            "ordered_key_count": 17,
            "ordered_key_root": foundation["candidate_authority"]["ordered_key_root"],
            "canary": foundation["candidate_authority"]["canary"],
            "budgets": foundation["candidate_authority"]["budgets"],
            "broker_contract_hash": broker_contract_hash(),
            "application_stage_order": list(APPLICATION_STAGES),
            "application_role_roots": application_role_roots,
            "positive_terminal_counts": {
                "net": rehearsal["positive"]["net_terminal_counts"],
                "all_in": rehearsal["positive"]["all_in_terminal_counts"],
            },
            "empty_terminal_counts": {
                "net": rehearsal["empty"]["net_terminal_counts"],
                "all_in": rehearsal["empty"]["all_in_terminal_counts"],
            },
            "recovery_matrix_root": canonical_hash(rehearsal["recovery_matrix"]),
            "engineering_blockers": blockers,
            "engineering_warnings": list(ENGINEERING_WARNINGS),
            "operational_state_unproven": True,
            "network_authorized": False,
            "authorization_eligible": False,
            "operator_authorization_required": True,
            "network_execution": offline_counters,
            "certification_blockers": list(CERTIFICATION_BLOCKERS),
            "readiness": {
                "single_canary_engineering_ready": status == READY_STATUS,
                "certification_ready": False,
                "portfolio_ready": False,
                "optimizer_ready": False,
                "paper_ready": False,
                "live_ready": False,
            },
        },
    )
    verification = write_immutable_generation(
        output / "final_verification",
        prefix="task055kr_final_verification",
        manifest_name="task055kr_final_verification.json",
        semantic={
            "schema_version": FINAL_VERIFICATION_SCHEMA,
            "status": "passed" if not blockers else "blocked",
            "top_status": status,
            "report_content_hash": report["content_hash"],
            "parent_verification_content_hash": foundation["parent_verification"][
                "content_hash"
            ],
            "source_seal_content_hash": foundation["source_seal"]["content_hash"],
            "historical_supersession_content_hash": foundation[
                "historical_supersession"
            ]["content_hash"],
            "candidate_authority_content_hash": foundation["candidate_authority"][
                "content_hash"
            ],
            "rehearsal_content_hash": rehearsal["content_hash"],
            "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
            "threat_model_content_hash": threat["content_hash"],
            "ordered_key_root": foundation["candidate_authority"]["ordered_key_root"],
            "broker_contract_hash": broker_contract_hash(),
            "application_role_roots": application_role_roots,
            "offline_counters": offline_counters,
            "application_stage_count": 12,
            "positive_net_terminal_pair_count": rehearsal["positive"][
                "net_terminal_pair_count"
            ],
            "positive_all_in_terminal_pair_count": rehearsal["positive"][
                "all_in_terminal_pair_count"
            ],
            "empty_net_terminal_pair_count": rehearsal["empty"][
                "net_terminal_pair_count"
            ],
            "empty_all_in_terminal_pair_count": rehearsal["empty"][
                "all_in_terminal_pair_count"
            ],
            "all_stage_boundaries_tested": rehearsal["recovery_matrix"][
                "all_stage_boundaries_tested"
            ],
        },
    )
    checkpoint = publish_candidate_checkpoint(
        authority=foundation["candidate_authority"],
        lineage={
            "parent_verification": foundation["parent_verification"]["content_hash"],
            "source_seal": foundation["source_seal"]["content_hash"],
            "historical_supersession": foundation["historical_supersession"][
                "content_hash"
            ],
            "native_rehearsal": rehearsal["content_hash"],
            "rehearsal_independent_verification": rehearsal_verification["content_hash"],
            "final_report": report["content_hash"],
            "final_independent_verification": verification["content_hash"],
            "threat_model": threat["content_hash"],
        },
        output_root=authority_root / "candidate_checkpoint",
    )
    final_seal = publish_final_candidate_seal(
        authority=foundation["candidate_authority"],
        checkpoint=checkpoint,
        source_seal=foundation["source_seal"],
        parent_verification=foundation["parent_verification"],
        supersession=foundation["historical_supersession"],
        rehearsal=rehearsal,
        rehearsal_verification=rehearsal_verification,
        report=report,
        final_verification=verification,
        broker_contract_hash=broker_contract_hash(),
        output_root=authority_root / "final_candidate_seal",
    )
    final_seal = validate_final_candidate_seal(
        final_seal["manifest_path"],
        repository_root=repository,
        reviewed_hash=final_seal["content_hash"],
    )
    scrubbed = _publish_scrubbed(
        foundation=foundation,
        rehearsal=rehearsal,
        rehearsal_verification=rehearsal_verification,
        threat=threat,
        report=report,
        verification=verification,
        checkpoint=checkpoint,
        final_seal=final_seal,
        application_role_roots=application_role_roots,
    )
    return report | {
        "parent_verification": foundation["parent_verification"],
        "source_seal": foundation["source_seal"],
        "historical_supersession": foundation["historical_supersession"],
        "candidate_authority": foundation["candidate_authority"],
        "candidate_checkpoint": checkpoint,
        "rehearsal": rehearsal,
        "rehearsal_verification": rehearsal_verification,
        "threat_model": threat,
        "final_verification": verification,
        "final_candidate_seal": final_seal,
        "scrubbed_evidence": scrubbed,
    }


def run_task055k(**_kwargs: Any) -> dict[str, Any]:
    raise Task055KRunError("task055kr_two_phase_runtime_then_evidence_workflow_required")


def _application_role_roots(rehearsal: Mapping[str, Any]) -> dict[str, str]:
    root = Path(rehearsal["manifest_path"]).parents[3]
    catalog = {row["role"]: row for row in rehearsal["artifact_catalog"]}
    positive = read_json(root / catalog["positive_primary_application"]["relative_path"])
    empty = read_json(root / catalog["empty_primary_application"]["relative_path"])
    positive_rows = {row["stage"]: row for row in positive["stages"]}
    empty_rows = {row["stage"]: row for row in empty["stages"]}
    if set(positive_rows) != set(APPLICATION_STAGES) or set(empty_rows) != set(
        APPLICATION_STAGES
    ):
        raise Task055KRunError("task055k_rehearsal_application_stage_set_invalid")
    return {
        name: canonical_hash(
            [
                positive_rows[name]["output_content_hash"],
                empty_rows[name]["output_content_hash"],
            ]
        )
        for name in APPLICATION_STAGES
    }


def _publish_scrubbed(
    *,
    foundation: Mapping[str, Any],
    rehearsal: Mapping[str, Any],
    rehearsal_verification: Mapping[str, Any],
    threat: Mapping[str, Any],
    report: Mapping[str, Any],
    verification: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    final_seal: Mapping[str, Any],
    application_role_roots: Mapping[str, str],
) -> dict[str, Any]:
    governed = Path(foundation["governed"])
    artifacts = [
        ("parent_verification", foundation["parent_verification"]),
        ("source_seal", foundation["source_seal"]),
        ("historical_supersession", foundation["historical_supersession"]),
        ("candidate_authority", foundation["candidate_authority"]),
        ("candidate_checkpoint", checkpoint),
        ("native_rehearsal", rehearsal),
        ("rehearsal_independent_verification", rehearsal_verification),
        ("threat_model", threat),
        ("final_report", report),
        ("final_independent_verification", verification),
        ("final_candidate_seal", final_seal),
    ]
    catalog = []
    for role, value in artifacts:
        path = Path(value["manifest_path"]).resolve()
        if governed not in path.parents:
            raise Task055KRunError(f"task055k_scrubbed_artifact_outside_governed:{role}")
        catalog.append(
            {
                "role": role,
                "relative_path": path.relative_to(governed).as_posix(),
                "sha256": sha256_file(path),
                "content_hash": value["content_hash"],
            }
        )
    catalog.sort(key=lambda row: row["role"])
    semantic = {
        "schema_version": SCRUBBED_SCHEMA,
        "status": report["status"],
        "implementation_commit": report["implementation_commit"],
        "baseline_commit": "cc44926dda583652c0dad260bacb62a75550cdda",
        "parent_task055j_final_seal_hash": foundation["parent"][
            "parent_final_execution_seal_content_hash"
        ],
        "ordered_exact_daily_keys": foundation["candidate_authority"][
            "ordered_exact_daily_keys"
        ],
        "ordered_key_root": foundation["candidate_authority"]["ordered_key_root"],
        "canary": foundation["candidate_authority"]["canary"],
        "budgets": foundation["candidate_authority"]["budgets"],
        "root_bindings": {
            "task_root": TASK055K_RELATIVE_ROOT,
            "authority_root": TASK055K_AUTHORITY_RELATIVE_ROOT,
            "historical_task055k_root": "validation_runs/task_055_k_20260719",
            "historical_task055k_authority_root": "governance/network_authority/task055k_single_canary_v1",
        },
        "source_entries": foundation["source_seal"]["entries"],
        "source_root": foundation["source_seal"]["source_root"],
        "application_stage_order": list(APPLICATION_STAGES),
        "application_role_roots": dict(application_role_roots),
        "synthetic_receipt_attestations": {
            "positive": rehearsal["positive"]["receipt_attestation"],
            "empty": rehearsal["empty"]["receipt_attestation"],
        },
        "artifact_catalog": catalog,
        "artifact_catalog_root": canonical_hash(catalog),
        "lineage": {role: value["content_hash"] for role, value in artifacts},
        "artifact_statuses": {role: value.get("status") for role, value in artifacts},
        "cross_lineage": {
            "checkpoint": checkpoint["lineage"],
            "final_seal_execution": final_seal["execution_lineage"],
            "final_seal_engineering": final_seal["engineering_validation"],
            "report": {
                "parent_verification": report["parent_verification_content_hash"],
                "source_seal": report["source_seal_content_hash"],
                "historical_supersession": report[
                    "historical_supersession_content_hash"
                ],
                "candidate_authority": report["candidate_authority_content_hash"],
                "native_rehearsal": report["rehearsal_content_hash"],
                "rehearsal_independent_verification": report[
                    "rehearsal_verification_content_hash"
                ],
                "threat_model": report["threat_model_content_hash"],
            },
            "final_verification": {
                "report": verification["report_content_hash"],
                "parent_verification": verification[
                    "parent_verification_content_hash"
                ],
                "source_seal": verification["source_seal_content_hash"],
                "historical_supersession": verification[
                    "historical_supersession_content_hash"
                ],
                "candidate_authority": verification[
                    "candidate_authority_content_hash"
                ],
                "native_rehearsal": verification["rehearsal_content_hash"],
                "rehearsal_independent_verification": verification[
                    "rehearsal_verification_content_hash"
                ],
                "threat_model": verification["threat_model_content_hash"],
            },
        },
        "broker_contract_hash": broker_contract_hash(),
        "threat_model": THREAT_MODEL,
        "network_authorized": False,
        "authorization_eligible": False,
        "operator_authorization_required": True,
        "operational_state_unproven": True,
        "engineering_blockers": list(report["engineering_blockers"]),
        "engineering_warnings": list(ENGINEERING_WARNINGS),
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "rehearsal_evidence_scope": "synthetic_rehearsal_only",
        "production_execution_ancestor": False,
        "network_execution": _offline_counters(rehearsal),
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
        "git_attestation_required": True,
    }
    payload = semantic | {"content_hash": canonical_hash(semantic)}
    path = Path(foundation["output"]) / "scrubbed_evidence/task055kr_scrubbed_evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and read_json(path) != payload:
        raise Task055KRunError("task055k_scrubbed_evidence_replacement_forbidden")
    if not path.exists():
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload | {"manifest_path": str(path)}


def _offline_counters(rehearsal: Mapping[str, Any]) -> dict[str, Any]:
    source = rehearsal.get("network_execution") or {}
    counters = {
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "gpu_job_count": 0,
        "prospective_holdout_accessed": source.get("prospective_holdout_accessed"),
        "max_read_date": source.get("max_read_date"),
        "read_ledger_file_count": source.get("read_ledger_file_count"),
        "read_ledger_row_count": source.get("read_ledger_row_count"),
        "read_ledger_root": source.get("read_ledger_root"),
    }
    if (
        counters["prospective_holdout_accessed"] is not False
        or not isinstance(counters["max_read_date"], str)
        or counters["max_read_date"] > "20260630"
        or int(counters["read_ledger_file_count"] or 0) <= 0
        or int(counters["read_ledger_row_count"] or 0) <= 0
        or not isinstance(counters["read_ledger_root"], str)
        or len(counters["read_ledger_root"]) != 64
    ):
        raise Task055KRunError("task055k_offline_read_boundary_invalid")
    return counters


def _require_clean(repository: Path) -> None:
    if _git(repository, "status", "--porcelain"):
        raise Task055KRunError("task055k_run_requires_clean_worktree")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, text=True, capture_output=True
    ).stdout.strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task055-KR offline evidence publisher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    foundation = subparsers.add_parser("foundation")
    foundation.add_argument("--repository-root", required=True)
    foundation.add_argument("--parent-task055j-final-seal", required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--repository-root", required=True)
    finalize.add_argument("--parent-task055j-final-seal", required=True)
    finalize.add_argument("--rehearsal-manifest", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "foundation":
            result = prepare_runtime_foundation(
                repository_root=args.repository_root,
                parent_task055j_final_seal=args.parent_task055j_final_seal,
            )
            summary = {
                "status": "foundation_published",
                "implementation_commit": result["implementation_commit"],
                "candidate_authority_content_hash": result["candidate_authority"][
                    "content_hash"
                ],
                "source_root": result["source_seal"]["source_root"],
            }
        else:
            result = finalize_offline_evidence(
                repository_root=args.repository_root,
                parent_task055j_final_seal=args.parent_task055j_final_seal,
                rehearsal_manifest=args.rehearsal_manifest,
            )
            summary = {
                "status": result["status"],
                "report_content_hash": result["content_hash"],
                "candidate_checkpoint_content_hash": result["candidate_checkpoint"][
                    "content_hash"
                ],
                "final_candidate_seal_content_hash": result["final_candidate_seal"][
                    "content_hash"
                ],
                "scrubbed_evidence_content_hash": result["scrubbed_evidence"][
                    "content_hash"
                ],
            }
    except Exception as exc:
        print(
            json.dumps({"status": BLOCKED_STATUS, "blocker": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

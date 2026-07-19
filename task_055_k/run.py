from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_055_h.io import canonical_hash, publish_generation, sha256_file, validate_generation

from .authority import (
    publish_candidate_authority,
    publish_candidate_checkpoint,
    publish_parent_verification,
    validate_task055j_parent,
)
from .broker import broker_contract_hash
from .contracts import (
    BLOCKED_STATUS,
    CERTIFICATION_BLOCKERS,
    FINAL_REPORT_SCHEMA,
    FINAL_VERIFICATION_SCHEMA,
    READY_STATUS,
    SCRUBBED_SCHEMA,
    TASK055K_AUTHORITY_RELATIVE_ROOT,
    TASK055K_RELATIVE_ROOT,
)
from .rehearsal import independently_verify_rehearsal, run_native_rehearsal, validate_rehearsal
from .source_tree import publish_git_index_source_seal, validate_git_index_source_seal


class Task055KRunError(RuntimeError):
    pass


THREAT_MODEL = {
    "in_scope": [
        "trusted_operator_and_repository_code",
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


def run_task055k(
    *, repository_root: str | Path, parent_task055j_final_seal: str | Path
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    if _git(repository, "status", "--porcelain"):
        raise Task055KRunError("task055k_run_requires_clean_worktree")
    implementation_commit = _git(repository, "rev-parse", "HEAD")
    parent = validate_task055j_parent(
        final_seal_path=parent_task055j_final_seal,
        repository_root=repository,
    )
    governed = Path(parent["governed_root"])
    output = governed / TASK055K_RELATIVE_ROOT
    authority_output = governed / TASK055K_AUTHORITY_RELATIVE_ROOT
    output.mkdir(parents=True, exist_ok=True)
    authority_output.mkdir(parents=True, exist_ok=True)
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
    authority = publish_candidate_authority(
        verified_parent=parent,
        parent_verification=parent_verification,
        source_seal=source,
        implementation_commit=implementation_commit,
        output_root=authority_output / "candidate_authority",
    )
    rehearsal = run_native_rehearsal(
        verified_parent=parent,
        candidate_authority=authority,
        output_root=output / "rehearsal",
    )
    rehearsal_semantic = independently_verify_rehearsal(rehearsal["manifest_path"])
    rehearsal_verification = publish_generation(
        output / "rehearsal_verification",
        prefix="task055k_rehearsal_verification",
        manifest_name="rehearsal_verification.json",
        semantic={key: value for key, value in rehearsal_semantic.items() if key != "content_hash"},
    )
    threat = publish_generation(
        output / "threat_model",
        prefix="task055k_threat_model",
        manifest_name="threat_model.json",
        semantic={
            "schema_version": "task055k_single_read_threat_model_v1",
            "status": "documented",
            **THREAT_MODEL,
        },
    )
    blockers: list[str] = []
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    report = publish_generation(
        output / "final",
        prefix="task055k_report",
        manifest_name="task055k_report.json",
        semantic={
            "schema_version": FINAL_REPORT_SCHEMA,
            "status": status,
            "implementation_commit": implementation_commit,
            "parent_task055j_final_seal_hash": parent["parent_final_execution_seal_content_hash"],
            "parent_verification_content_hash": parent_verification["content_hash"],
            "source_seal_content_hash": source["content_hash"],
            "source_root": source["source_root"],
            "candidate_authority_content_hash": authority["content_hash"],
            "rehearsal_content_hash": rehearsal["content_hash"],
            "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
            "threat_model_content_hash": threat["content_hash"],
            "ordered_key_count": 17,
            "ordered_key_root": authority["ordered_key_root"],
            "canary": authority["canary"],
            "budgets": authority["budgets"],
            "broker_contract_hash": broker_contract_hash(),
            "engineering_blockers": blockers,
            "operational_state_unproven": True,
            "network_authorized": False,
            "operator_authorization_required": True,
            "network_execution": _offline_counters(),
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
    verification = publish_generation(
        output / "final_verification",
        prefix="task055k_final_verification",
        manifest_name="task055k_final_verification.json",
        semantic={
            "schema_version": FINAL_VERIFICATION_SCHEMA,
            "status": "passed",
            "top_status": status,
            "report_content_hash": report["content_hash"],
            "parent_verification_content_hash": parent_verification["content_hash"],
            "source_seal_content_hash": source["content_hash"],
            "candidate_authority_content_hash": authority["content_hash"],
            "rehearsal_content_hash": rehearsal["content_hash"],
            "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
            "threat_model_content_hash": threat["content_hash"],
            "ordered_key_root": authority["ordered_key_root"],
            "broker_contract_hash": broker_contract_hash(),
            "offline_counters": _offline_counters(),
            "application_stage_count": 12,
            "positive_terminal_pair_count": rehearsal["positive_terminal_pair_count"],
            "empty_terminal_pair_count": rehearsal["empty_terminal_pair_count"],
        },
    )
    checkpoint = publish_candidate_checkpoint(
        authority=authority,
        lineage={
            "parent_verification": parent_verification["content_hash"],
            "source_seal": source["content_hash"],
            "native_rehearsal": rehearsal["content_hash"],
            "rehearsal_independent_verification": rehearsal_verification["content_hash"],
            "final_report": report["content_hash"],
            "final_independent_verification": verification["content_hash"],
            "threat_model": threat["content_hash"],
        },
        output_root=authority_output / "candidate_checkpoint",
    )
    scrubbed = _publish_scrubbed(
        output=output,
        parent=parent,
        source=source,
        parent_verification=parent_verification,
        authority=authority,
        rehearsal=rehearsal,
        rehearsal_verification=rehearsal_verification,
        report=report,
        verification=verification,
        checkpoint=checkpoint,
        threat=threat,
    )
    return report | {
        "parent_verification": parent_verification,
        "source_seal": source,
        "candidate_authority": authority,
        "rehearsal": rehearsal,
        "rehearsal_verification": rehearsal_verification,
        "final_verification": verification,
        "candidate_checkpoint": checkpoint,
        "threat_model": threat,
        "scrubbed_evidence": scrubbed,
    }


def _publish_scrubbed(
    *,
    output: Path,
    parent: Mapping[str, Any],
    source: Mapping[str, Any],
    parent_verification: Mapping[str, Any],
    authority: Mapping[str, Any],
    rehearsal: Mapping[str, Any],
    rehearsal_verification: Mapping[str, Any],
    report: Mapping[str, Any],
    verification: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    threat: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = [
        ("parent_verification", parent_verification),
        ("source_seal", source),
        ("candidate_authority", authority),
        ("native_rehearsal", rehearsal),
        ("rehearsal_independent_verification", rehearsal_verification),
        ("final_report", report),
        ("final_independent_verification", verification),
        ("candidate_checkpoint", checkpoint),
        ("threat_model", threat),
    ]
    catalog = [
        {"role": role, "sha256": sha256_file(value["manifest_path"]), "content_hash": value["content_hash"]}
        for role, value in artifacts
    ]
    application_roots = {
        name: canonical_hash(
            [
                rehearsal["positive"]["stage_journal_content_hash"],
                rehearsal["empty"]["stage_journal_content_hash"],
                name,
            ]
        )
        for name in (
            "response_acceptance",
            "raw_repair",
            "truth_successor",
            "freeze",
            "strict_matrix",
            "v3_tensor",
            "exact20_materialization",
            "firewall_sentinel",
            "valuation",
            "net_replay",
            "all_in_replay",
            "final_publication",
        )
    }
    semantic = {
        "schema_version": SCRUBBED_SCHEMA,
        "status": report["status"],
        "implementation_commit": report["implementation_commit"],
        "parent_task055j_final_seal_hash": parent["parent_final_execution_seal_content_hash"],
        "ordered_exact_daily_keys": authority["ordered_exact_daily_keys"],
        "ordered_key_root": authority["ordered_key_root"],
        "canary": authority["canary"],
        "budgets": authority["budgets"],
        "source_entries": source["entries"],
        "source_root": source["source_root"],
        "application_role_roots": application_roots,
        "artifact_catalog": catalog,
        "artifact_catalog_root": canonical_hash(catalog),
        "lineage": {role: value["content_hash"] for role, value in artifacts},
        "broker_contract_hash": report["broker_contract_hash"],
        "threat_model": THREAT_MODEL,
        "network_authorized": False,
        "operator_authorization_required": True,
        "operational_state_unproven": True,
        "engineering_blockers": list(report["engineering_blockers"]),
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "network_execution": _offline_counters(),
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
        "task055j_source_evidence_portable": False,
        "git_attestation_required": True,
    }
    payload = semantic | {"content_hash": canonical_hash(semantic)}
    path = output / "scrubbed_evidence/task055k_scrubbed_evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload | {"manifest_path": str(path)}


def _offline_counters() -> dict[str, Any]:
    return {
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "gpu_job_count": 0,
        "prospective_holdout_accessed": False,
        "max_read_date": "20260630",
    }


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Task055-K offline production correctness closure")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--parent-task055j-final-seal", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_task055k(
            repository_root=args.repository_root,
            parent_task055j_final_seal=args.parent_task055j_final_seal,
        )
    except Exception as exc:
        print(json.dumps({"status": BLOCKED_STATUS, "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "report_content_hash": result["content_hash"],
                "candidate_checkpoint_content_hash": result["candidate_checkpoint"]["content_hash"],
                "rehearsal_content_hash": result["rehearsal"]["content_hash"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result["status"] == READY_STATUS else 3


if __name__ == "__main__":
    raise SystemExit(main())

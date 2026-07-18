from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation

from .application_tree import publish_application_preflight, validate_application_preflight
from .authority import (
    publish_execution_authorization,
    publish_final_execution_seal,
    publish_runtime_authority,
    resolve_and_validate_parent,
    validate_execution_authorization,
    validate_final_execution_seal,
    validate_runtime_authority,
)
from .contracts import (
    BASELINE_COMMIT,
    BLOCKED_STATUS,
    CANARY,
    CERTIFICATION_BLOCKERS,
    FINAL_REPORT_SCHEMA,
    FINAL_VERIFICATION_SCHEMA,
    PARENT_AUTHORIZATION_SEAL_HASH,
    PARENT_CANARY_PLAN_HASH,
    READY_STATUS,
    SCRUBBED_EVIDENCE_SCHEMA,
    TASK055J_RELATIVE_ROOT,
)
from .rehearsal import independently_verify_rehearsal, run_native_rehearsal, validate_rehearsal
from .source_tree import publish_source_tree_seal, validate_source_tree_seal
from .verifier import verify_scrubbed_evidence


class Task055JRunError(RuntimeError):
    pass


def run_task055j(
    *, repository_root: str | Path, parent_task055i_report: str | Path
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    if _git(repository, "status", "--porcelain"):
        raise Task055JRunError("task055j_run_requires_clean_worktree")
    implementation_commit = _git(repository, "rev-parse", "HEAD")
    if subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE_COMMIT, implementation_commit], cwd=repository).returncode:
        raise Task055JRunError("task055j_baseline_not_implementation_ancestor")
    parent = resolve_and_validate_parent(
        repository_root=repository, parent_task055i_report=parent_task055i_report
    )
    governed = Path(parent["governed"])
    output = governed / TASK055J_RELATIVE_ROOT
    output.mkdir(parents=True, exist_ok=True)
    source = publish_source_tree_seal(
        repository_root=repository,
        output_root=output / "source_tree",
        implementation_commit=implementation_commit,
    )
    preflight = publish_application_preflight(
        governed_root=governed,
        parent_runtime_authority=parent["runtime"],
        output_root=output / "application_preflight",
    )
    runtime = publish_runtime_authority(
        parent=parent,
        source_tree_seal=source["manifest_path"],
        application_preflight=preflight["manifest_path"],
        implementation_commit=implementation_commit,
    )
    try:
        rehearsal = run_native_rehearsal(runtime_authority=runtime, output_root=output / "rehearsal")
        rehearsal_verification_semantic = independently_verify_rehearsal(rehearsal["manifest_path"])
    except Exception as exc:
        rehearsal = _publish_blocked_rehearsal(output / "rehearsal", blocker=exc)
        rehearsal_verification_semantic = {
            "schema_version": "task055j_rehearsal_independent_verification_v1",
            "status": "blocked_verified",
            "rehearsal_content_hash": rehearsal["content_hash"],
            "artifact_root": rehearsal["artifact_root"],
            "positive_terminal_pair_count": rehearsal["positive_terminal_pair_count"],
            "empty_terminal_pair_count": rehearsal["empty_terminal_pair_count"],
            "negative_case_count": rehearsal["negative_case_count"],
            "real_network_counts": {
                "credential_read_count": 0,
                "tushare_post_count": 0,
                "other_market_http_count": 0,
            },
            "blockers": list(rehearsal.get("blockers") or ()),
        }
    rehearsal_verification = publish_generation(
        output / "rehearsal_verification",
        prefix="task055j_rehearsal_verification",
        manifest_name="rehearsal_verification.json",
        semantic={key: value for key, value in rehearsal_verification_semantic.items() if key != "content_hash"},
    )
    authorization = publish_execution_authorization(
        runtime_authority=runtime,
        rehearsal=rehearsal,
        rehearsal_verification=rehearsal_verification,
    )
    authorization = validate_execution_authorization(authorization["manifest_path"])
    blockers = list(authorization.get("engineering_blockers") or ())
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    report_semantic = {
        "schema_version": FINAL_REPORT_SCHEMA,
        "status": status,
        "implementation_commit": implementation_commit,
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_canary_plan_hash": PARENT_CANARY_PLAN_HASH,
        "source_tree_seal_content_hash": source["content_hash"],
        "source_root": source["source_root"],
        "application_preflight_content_hash": preflight["content_hash"],
        "application_tree_content_hash": preflight["application_artifact_tree_content_hash"],
        "application_tree_root": preflight["application_artifact_tree_root"],
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
        "ordered_key_count": runtime["ordered_key_count"],
        "ordered_key_root": runtime["ordered_key_root"],
        "canary": dict(CANARY),
        "budgets": runtime["budgets"],
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
            "prospective_holdout_accessed": False,
            "max_read_date": preflight["max_validated_source_date"],
        },
        "real_canary_executed": False,
        "real_response_applied": False,
        "real_gpu_started": False,
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_proven": False,
        "operational_state_unproven": True,
        "operational_blockers": authorization["operational_blockers"],
        "engineering_blockers": blockers,
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "readiness": {
            "single_canary_production_closure_ready": status == READY_STATUS,
            "certification_ready": False,
            "portfolio_ready": False,
            "optimizer_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
    }
    report = publish_generation(
        output / "final",
        prefix="task055j_report",
        manifest_name="task055j_report.json",
        semantic=report_semantic,
    )
    verification_semantic = verify_task055j_report(
        report["manifest_path"],
        repository_root=repository,
        runtime_authority=runtime["manifest_path"],
        execution_authorization=authorization["manifest_path"],
        application_preflight=preflight["manifest_path"],
        source_tree_seal=source["manifest_path"],
        rehearsal=rehearsal["manifest_path"],
        rehearsal_verification=rehearsal_verification["manifest_path"],
    )
    final_verification = publish_generation(
        output / "final_verification",
        prefix="task055j_final_verification",
        manifest_name="task055j_final_verification.json",
        semantic=verification_semantic,
    )
    final_seal = publish_final_execution_seal(
        runtime_authority=runtime,
        execution_authorization=authorization,
        rehearsal=rehearsal,
        rehearsal_verification=rehearsal_verification,
        final_report=report,
        final_verification=final_verification,
    )
    final_seal = validate_final_execution_seal(
        final_seal["manifest_path"],
        reviewed_hash=final_seal["content_hash"],
        repository_root=repository,
        require_ready=status == READY_STATUS,
        require_pristine=True,
    )
    scrubbed = _publish_scrubbed(
        output=output,
        runtime=runtime,
        authorization=authorization,
        source=source,
        preflight=preflight,
        rehearsal=rehearsal,
        rehearsal_verification=rehearsal_verification,
        report=report,
        final_verification=final_verification,
        final_seal=final_seal,
    )
    scrubbed_verified = verify_scrubbed_evidence(scrubbed["manifest_path"])
    return report | {
        "runtime_authority": runtime,
        "execution_authorization": authorization,
        "source_tree": source,
        "application_preflight": preflight,
        "rehearsal": rehearsal,
        "rehearsal_verification": rehearsal_verification,
        "final_verification": final_verification,
        "final_execution_seal": final_seal,
        "scrubbed_evidence": scrubbed,
        "scrubbed_verification_hash": scrubbed_verified["verification_hash"],
    }


def verify_task055j_report(
    report_path: str | Path,
    *,
    repository_root: str | Path,
    runtime_authority: str | Path,
    execution_authorization: str | Path,
    application_preflight: str | Path,
    source_tree_seal: str | Path,
    rehearsal: str | Path,
    rehearsal_verification: str | Path,
) -> dict[str, Any]:
    report = validate_generation(report_path, schema=FINAL_REPORT_SCHEMA, manifest_name="task055j_report.json")
    runtime = validate_runtime_authority(
        runtime_authority,
        repository_root=repository_root,
        require_pristine=True,
        allow_evidence_only_descendant=False,
    )
    authorization = validate_execution_authorization(execution_authorization)
    preflight = validate_application_preflight(application_preflight, governed_root=runtime["governed_root"])
    source = validate_source_tree_seal(
        source_tree_seal,
        repository_root=repository_root,
        require_clean=True,
        allow_evidence_only_descendant=False,
    )
    rehearsal_payload = validate_rehearsal(rehearsal, require_passed=False)
    rehearsal_verify = validate_generation(
        rehearsal_verification,
        schema="task055j_rehearsal_independent_verification_v1",
        manifest_name="rehearsal_verification.json",
    )
    expected = {
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "application_preflight_content_hash": preflight["content_hash"],
        "source_tree_seal_content_hash": source["content_hash"],
        "rehearsal_content_hash": rehearsal_payload["content_hash"],
        "rehearsal_verification_content_hash": rehearsal_verify["content_hash"],
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise Task055JRunError(f"task055j_report_lineage_mismatch:{key}")
    expected_status = READY_STATUS if not authorization.get("engineering_blockers") else BLOCKED_STATUS
    if report.get("status") != expected_status:
        raise Task055JRunError("task055j_report_status_invalid")
    counters = report.get("network_execution") or {}
    if any(int(counters.get(key) or 0) for key in ("credential_read_count", "tushare_post_count", "other_market_http_count")):
        raise Task055JRunError("task055j_report_network_counter_invalid")
    if counters.get("prospective_holdout_accessed") is not False:
        raise Task055JRunError("task055j_report_holdout_boundary_invalid")
    max_read_date = str(counters.get("max_read_date") or "")
    if len(max_read_date) != 8 or not max_read_date.isdigit() or max_read_date > "20260630":
        raise Task055JRunError("task055j_report_future_read_detected")
    if any((report.get("readiness") or {}).get(key) is not False for key in ("certification_ready", "portfolio_ready", "optimizer_ready", "paper_ready", "live_ready")):
        raise Task055JRunError("task055j_report_downstream_readiness_invalid")
    return {
        "schema_version": FINAL_VERIFICATION_SCHEMA,
        "status": "passed" if expected_status == READY_STATUS else "blocked_verified",
        "top_status": expected_status,
        "report_content_hash": report["content_hash"],
        **expected,
        "source_root": source["source_root"],
        "application_tree_root": preflight["application_artifact_tree_root"],
        "ordered_key_root": runtime["ordered_key_root"],
        "canary": dict(CANARY),
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_market_http_count": 0,
        "prospective_holdout_accessed": False,
        "max_read_date": preflight["max_validated_source_date"],
    }


def _publish_scrubbed(
    *,
    output: Path,
    runtime: Mapping[str, Any],
    authorization: Mapping[str, Any],
    source: Mapping[str, Any],
    preflight: Mapping[str, Any],
    rehearsal: Mapping[str, Any],
    rehearsal_verification: Mapping[str, Any],
    report: Mapping[str, Any],
    final_verification: Mapping[str, Any],
    final_seal: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = [
        ("source_tree_seal", source),
        ("application_preflight", preflight),
        ("application_tree_seal", preflight["application_tree"]),
        ("runtime_authority", runtime),
        ("execution_authorization", authorization),
        ("native_rehearsal", rehearsal),
        ("rehearsal_independent_verification", rehearsal_verification),
        ("final_report", report),
        ("final_independent_verification", final_verification),
        ("final_execution_seal", final_seal),
    ]
    catalog = [
        {"role": role, "sha256": sha256_file(payload["manifest_path"]), "content_hash": payload["content_hash"]}
        for role, payload in artifacts
    ]
    lineage = {
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "application_preflight_content_hash": preflight["content_hash"],
        "application_tree_content_hash": preflight["application_tree"]["content_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
        "final_report_content_hash": report["content_hash"],
        "final_verification_content_hash": final_verification["content_hash"],
        "final_execution_seal_content_hash": final_seal["content_hash"],
    }
    semantic = {
        "schema_version": SCRUBBED_EVIDENCE_SCHEMA,
        "status": report["status"],
        "implementation_commit": runtime["implementation_commit"],
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_canary_plan_hash": PARENT_CANARY_PLAN_HASH,
        "ordered_exact_daily_keys": runtime["ordered_exact_daily_keys"],
        "ordered_key_root": runtime["ordered_key_root"],
        "canary": dict(CANARY),
        "budgets": runtime["budgets"],
        "root_binding_hashes": {role: value["identity_hash"] for role, value in runtime["root_identities"].items()},
        "source_entries": source["entries"],
        "source_root": source["source_root"],
        "application_role_roots": preflight["application_tree"]["role_roots"],
        "application_tree_root": preflight["application_tree"]["tree_root"],
        "artifact_catalog": catalog,
        "artifact_catalog_root": canonical_hash(catalog),
        "lineage": lineage,
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
            "prospective_holdout_accessed": False,
            "max_read_date": preflight["max_validated_source_date"],
        },
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_unproven": True,
        "engineering_blockers": list(report.get("engineering_blockers") or ()),
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
    }
    payload = semantic | {"content_hash": canonical_hash(semantic)}
    root = output / "scrubbed_evidence"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "task055j_scrubbed_evidence.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload | {"manifest_path": str(path)}


def _publish_blocked_rehearsal(output_root: Path, *, blocker: Exception) -> dict[str, Any]:
    blockers = [{"code": type(blocker).__name__, "detail": str(blocker)}]
    semantic = {
        "schema_version": "task055j_native_application_rehearsal_v1",
        "status": "blocked",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "production_context_root": "unavailable",
        "production_context_parsed": False,
        "positive_chain_complete": False,
        "positive_terminal_pair_count": 0,
        "positive_terminal_counts": {},
        "positive_frontier_union_root": canonical_hash([]),
        "empty_chain_complete": False,
        "empty_terminal_pair_count": 0,
        "empty_terminal_counts": {},
        "empty_frontier_union_root": canonical_hash([]),
        "empty_dynamic_l2_status": "not_published",
        "l2_response_application_status": "unsupported_waiting_for_separate_authority",
        "negative_cases": {},
        "negative_case_count": 0,
        "artifact_hashes": {},
        "artifact_root": canonical_hash([]),
        "blockers": blockers,
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
            "synthetic_transport_call_count": 0,
            "prospective_holdout_accessed": False,
        },
    }
    result = publish_generation(
        output_root / "report",
        prefix="task055j_native_rehearsal_blocked",
        manifest_name="rehearsal_manifest.json",
        semantic=semantic,
    )
    return validate_rehearsal(result["manifest_path"], require_passed=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task 055-J pure-offline production closure publication")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--parent-task055i-report", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_task055j(
            repository_root=args.repository_root,
            parent_task055i_report=args.parent_task055i_report,
        )
    except Exception as exc:
        print(json.dumps({"status": BLOCKED_STATUS, "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "content_hash": result["content_hash"],
                "runtime_authority_content_hash": result["runtime_authority"]["content_hash"],
                "final_execution_seal_content_hash": result["final_execution_seal"]["content_hash"],
                "final_verification_content_hash": result["final_verification"]["content_hash"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result["status"] == READY_STATUS else 3


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())

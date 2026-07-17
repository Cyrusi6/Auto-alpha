from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation

from .authority import (
    publish_task055i_authority,
    validate_execution_authorization,
    validate_runtime_authority,
)
from .contracts import (
    BLOCKED_STATUS,
    CANARY,
    CERTIFICATION_BLOCKERS,
    FINAL_REPORT_SCHEMA,
    FINAL_VERIFICATION_SCHEMA,
    PARENT_AUTHORIZATION_SEAL_HASH,
    PARENT_CANARY_PLAN_HASH,
    PARENT_GIT_EVIDENCE_HASH,
    READY_STATUS,
    SCRUBBED_EVIDENCE_SCHEMA,
    TASK055I_RELATIVE_ROOT,
)
from .rehearsal import run_native_application_rehearsal
from .verifier import verify_scrubbed_evidence


class Task055IRunError(RuntimeError):
    pass


def run_task055i(*, repository_root: str | Path, governed_root: str | Path) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    output = governed / TASK055I_RELATIVE_ROOT
    output.mkdir(parents=True, exist_ok=True)
    implementation_commit = _git(repository, "rev-parse", "HEAD")
    if _git(repository, "status", "--porcelain"):
        raise Task055IRunError("task055i_run_requires_clean_source_tree")
    rehearsal = run_native_application_rehearsal(output / "rehearsal")
    authority = publish_task055i_authority(
        repository_root=repository,
        governed_root=governed,
        output_root=output,
        implementation_commit=implementation_commit,
        rehearsal_manifest=rehearsal["manifest_path"],
    )
    execution_authorization = validate_execution_authorization(authority["manifest_path"])
    runtime = validate_runtime_authority(authority["runtime_authority"]["manifest_path"], require_pristine=True)
    scrubbed = _publish_scrubbed(
        output=output,
        implementation_commit=implementation_commit,
        runtime=runtime,
        authorization=execution_authorization,
        rehearsal=rehearsal,
    )
    scrubbed_verified = verify_scrubbed_evidence(scrubbed["manifest_path"])
    blockers = list(execution_authorization.get("engineering_blockers") or ())
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    report_semantic = {
        "schema_version": FINAL_REPORT_SCHEMA,
        "status": status,
        "implementation_commit": implementation_commit,
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_git_evidence_hash": PARENT_GIT_EVIDENCE_HASH,
        "single_request_plan_hash": PARENT_CANARY_PLAN_HASH,
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": execution_authorization["content_hash"],
        "scrubbed_evidence_content_hash": scrubbed["content_hash"],
        "scrubbed_verification_hash": scrubbed_verified["verification_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "rehearsal_artifact_root": rehearsal["artifact_root"],
        "canary": dict(CANARY),
        "budgets": runtime["budgets"],
        "network_execution": {
            "credential_read_count": 0,
            "tushare_request_count": 0,
            "other_network_request_count": 0,
            "prospective_holdout_accessed": False,
        },
        "real_canary_executed": False,
        "real_response_applied": False,
        "real_gpu_started": False,
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_proven": False,
        "operational_state_unproven": True,
        "operational_blockers": execution_authorization["operational_blockers"],
        "engineering_blockers": blockers,
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "readiness": {
            "single_canary_execution_ready": status == READY_STATUS,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
    }
    report = publish_generation(
        output / "final",
        prefix="task055i_report",
        manifest_name="task055i_report.json",
        semantic=report_semantic,
    )
    verification = verify_task055i_report(
        report["manifest_path"],
        runtime_authority=authority["runtime_authority"]["manifest_path"],
        execution_authorization=authority["manifest_path"],
        scrubbed_evidence=scrubbed["manifest_path"],
        rehearsal_manifest=rehearsal["manifest_path"],
    )
    final = publish_generation(
        output / "final_verification",
        prefix="task055i_final_verification",
        manifest_name="task055i_final_verification.json",
        semantic=verification,
    )
    return report | {
        "runtime_authority": authority["runtime_authority"],
        "execution_authorization": authority,
        "scrubbed_evidence": scrubbed,
        "rehearsal": rehearsal,
        "final_verification": final,
    }


def verify_task055i_report(
    report_path: str | Path,
    *,
    runtime_authority: str | Path,
    execution_authorization: str | Path,
    scrubbed_evidence: str | Path,
    rehearsal_manifest: str | Path,
) -> dict[str, Any]:
    report = validate_generation(report_path, schema=FINAL_REPORT_SCHEMA, manifest_name="task055i_report.json")
    runtime = validate_runtime_authority(runtime_authority, require_pristine=True)
    authorization = validate_execution_authorization(execution_authorization)
    scrubbed = verify_scrubbed_evidence(scrubbed_evidence)
    rehearsal = read_json(rehearsal_manifest)
    rehearsal_semantic = {key: value for key, value in rehearsal.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(rehearsal_semantic) != rehearsal.get("content_hash") or rehearsal.get("status") != "passed":
        raise Task055IRunError("task055i_report_rehearsal_invalid")
    expected = {
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "scrubbed_evidence_content_hash": scrubbed["content_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise Task055IRunError(f"task055i_report_lineage_mismatch:{key}")
    expected_status = READY_STATUS if not authorization.get("engineering_blockers") else BLOCKED_STATUS
    if report.get("status") != expected_status:
        raise Task055IRunError("task055i_report_status_mismatch")
    counters = report.get("network_execution") or {}
    if any(int(counters.get(key) or 0) for key in ("credential_read_count", "tushare_request_count", "other_network_request_count")):
        raise Task055IRunError("task055i_report_offline_counter_invalid")
    if counters.get("prospective_holdout_accessed") is not False:
        raise Task055IRunError("task055i_report_holdout_boundary_invalid")
    if any(report.get(key) is not False for key in ("real_canary_executed", "real_response_applied", "real_gpu_started", "resume_authorized", "batch_authorized")):
        raise Task055IRunError("task055i_report_execution_boundary_invalid")
    if report.get("operational_state_unproven") is not True:
        raise Task055IRunError("task055i_report_operational_boundary_invalid")
    if any((report.get("readiness") or {}).get(key) is not False for key in ("certification_ready", "portfolio_ready", "paper_ready", "live_ready")):
        raise Task055IRunError("task055i_report_downstream_readiness_invalid")
    return {
        "schema_version": FINAL_VERIFICATION_SCHEMA,
        "status": "passed" if expected_status == READY_STATUS else "blocked_verified",
        "top_status": expected_status,
        "report_content_hash": report["content_hash"],
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "scrubbed_verification_hash": scrubbed["verification_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "canary": report["canary"],
        "credential_read_count": 0,
        "tushare_request_count": 0,
        "other_network_request_count": 0,
        "prospective_holdout_accessed": False,
        "resume_authorized": False,
    }


def _publish_scrubbed(
    *,
    output: Path,
    implementation_commit: str,
    runtime: dict[str, Any],
    authorization: dict[str, Any],
    rehearsal: dict[str, Any],
) -> dict[str, Any]:
    catalog = [
        {"role": "parent_authorization_seal", "sha256": PARENT_AUTHORIZATION_SEAL_HASH, "content_hash": PARENT_AUTHORIZATION_SEAL_HASH},
        {"role": "parent_git_evidence", "sha256": PARENT_GIT_EVIDENCE_HASH, "content_hash": PARENT_GIT_EVIDENCE_HASH},
        {"role": "single_request_plan", "sha256": PARENT_CANARY_PLAN_HASH, "content_hash": PARENT_CANARY_PLAN_HASH},
        {"role": "runtime_authority", "sha256": sha256_file(runtime["manifest_path"]), "content_hash": runtime["content_hash"]},
        {"role": "execution_authorization", "sha256": sha256_file(authorization["manifest_path"]), "content_hash": authorization["content_hash"]},
        {"role": "native_rehearsal", "sha256": sha256_file(rehearsal["manifest_path"]), "content_hash": rehearsal["content_hash"]},
    ]
    semantic = {
        "schema_version": SCRUBBED_EVIDENCE_SCHEMA,
        "status": authorization["status"],
        "implementation_commit": implementation_commit,
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_git_evidence_hash": PARENT_GIT_EVIDENCE_HASH,
        "single_request_plan_hash": PARENT_CANARY_PLAN_HASH,
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "rehearsal_artifact_root": rehearsal["artifact_root"],
        "semantic_source_root": runtime["semantic_source_root"],
        "canary": dict(CANARY),
        "ordered_exact_daily_key_count": 17,
        "ordered_exact_daily_keys": [
            {key: row[key] for key in ("ordinal", "api_name", "ts_code", "trade_date", "fields", "transport_hash", "evidence_use_hash")}
            for row in runtime["ordered_exact_daily_keys"]
        ],
        "budgets": runtime["budgets"],
        "root_binding_hashes": {
            role: value["identity_hash"]
            for role, value in runtime["root_identities"].items()
        },
        "initial_network_ledger_root": runtime["initial_network_ledger"]["root"],
        "initial_transport_spend_root": runtime["initial_transport_spend"]["root"],
        "artifact_catalog": catalog,
        "artifact_catalog_root": canonical_hash(catalog),
        "network_execution": {
            "credential_read_count": 0,
            "tushare_request_count": 0,
            "other_network_request_count": 0,
            "prospective_holdout_accessed": False,
        },
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_unproven": True,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
    }
    content_hash = canonical_hash(semantic)
    payload = semantic | {"content_hash": content_hash}
    root = output / "scrubbed_evidence"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "task055i_scrubbed_evidence.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload | {"manifest_path": str(path)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task 055-I pure-offline execution authorization publication")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--governed-root", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_task055i(repository_root=args.repository_root, governed_root=args.governed_root)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": result["status"],
        "content_hash": result["content_hash"],
        "runtime_authority_content_hash": result["runtime_authority"]["content_hash"],
        "execution_authorization_content_hash": result["execution_authorization"]["content_hash"],
        "final_verification_content_hash": result["final_verification"]["content_hash"],
    }, indent=2, sort_keys=True))
    return 0 if result["status"] == READY_STATUS else 3


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())

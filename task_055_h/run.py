from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from .authorization import publish_authorization_seal, validate_authorization_seal, verify_scrubbed_evidence_package
from .contracts import BLOCKED_STATUS, FINAL_REPORT_SCHEMA, FINAL_VERIFICATION_SCHEMA, READY_STATUS, TASK055H_RELATIVE_ROOT
from .io import canonical_hash, publish_generation, validate_generation


class Task055HRunError(RuntimeError):
    pass


def run_task055h(*, repository_root: str | Path, governed_root: str | Path) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    output = governed / TASK055H_RELATIVE_ROOT
    output.mkdir(parents=True, exist_ok=True)
    implementation_commit = _git(repository, "rev-parse", "HEAD")
    seal = publish_authorization_seal(
        repository_root=repository,
        governed_root=governed,
        output_root=output,
        implementation_commit=implementation_commit,
    )
    blockers = list(seal.get("engineering_blockers") or ())
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    execution = seal.get("network_execution") or {}
    report_semantic = {
        "schema_version": FINAL_REPORT_SCHEMA,
        "status": status,
        "implementation_commit": implementation_commit,
        "authorization_seal_content_hash": seal["content_hash"],
        "scrubbed_evidence_content_hash": seal["scrubbed_evidence"]["content_hash"],
        "fee_attestation_content_hash": None if seal.get("fee_attestation") is None else seal["fee_attestation"].get("content_hash"),
        "operational_seal_content_hash": None if seal.get("operational_seal") is None else seal["operational_seal"].get("content_hash"),
        "frontier_count": seal["ordered_exact_daily_key_count"],
        "frontier_root": seal["frontier_root"],
        "plan_hash": seal["task055g_plan_hash"],
        "canary": seal["canary"],
        "credential_read_count": int(execution.get("credential_read_count") or 0),
        "tushare_request_count": int(execution.get("tushare_request_count") or 0),
        "other_network_request_count": int(execution.get("other_network_request_count") or 0),
        "prospective_holdout_accessed": bool(execution.get("prospective_holdout_accessed")),
        "resume_authorized": False,
        "engineering_blockers": blockers,
        "readiness": {
            "canary_authorization_ready": status == READY_STATUS,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
    }
    report = publish_generation(output / "final", prefix="task055h_report", manifest_name="task055h_report.json", semantic=report_semantic)
    verification = verify_task055h_report(report["manifest_path"], authorization_seal=seal["manifest_path"], scrubbed_evidence=seal["scrubbed_evidence"]["manifest_path"])
    verified = publish_generation(output / "final_verification", prefix="task055h_final_verification", manifest_name="task055h_final_verification.json", semantic=verification)
    return report | {"authorization_seal": seal, "final_verification": verified}


def verify_task055h_report(report_path: str | Path, *, authorization_seal: str | Path, scrubbed_evidence: str | Path) -> dict[str, Any]:
    report = validate_generation(report_path, schema=FINAL_REPORT_SCHEMA, manifest_name="task055h_report.json")
    seal = validate_authorization_seal(authorization_seal, require_ready=False)
    scrubbed = verify_scrubbed_evidence_package(scrubbed_evidence)
    if report["authorization_seal_content_hash"] != seal["content_hash"] or report["scrubbed_evidence_content_hash"] != scrubbed["package_content_hash"]:
        raise Task055HRunError("task055h_report_lineage_mismatch")
    expected_status = READY_STATUS if not seal.get("engineering_blockers") else BLOCKED_STATUS
    if report.get("status") != expected_status:
        raise Task055HRunError("task055h_report_status_mismatch")
    for key in ("credential_read_count", "tushare_request_count", "other_network_request_count"):
        if int(report.get(key) or 0) != 0:
            raise Task055HRunError(f"task055h_offline_counter_invalid:{key}")
    if report.get("prospective_holdout_accessed") is not False or report.get("resume_authorized") is not False:
        raise Task055HRunError("task055h_boundary_invalid")
    semantic = {
        "schema_version": FINAL_VERIFICATION_SCHEMA,
        "status": "passed" if expected_status == READY_STATUS else "blocked_verified",
        "top_status": expected_status,
        "report_content_hash": report["content_hash"],
        "authorization_seal_content_hash": seal["content_hash"],
        "scrubbed_evidence_verification_hash": scrubbed["content_hash"],
        "frontier_count": report["frontier_count"],
        "frontier_root": report["frontier_root"],
        "plan_hash": report["plan_hash"],
        "credential_read_count": int(report["credential_read_count"]),
        "tushare_request_count": int(report["tushare_request_count"]),
        "other_network_request_count": int(report["other_network_request_count"]),
        "prospective_holdout_accessed": bool(report["prospective_holdout_accessed"]),
    }
    return semantic


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task 055-H pure-offline authorization plane")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--governed-root", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_task055h(repository_root=args.repository_root, governed_root=args.governed_root)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": result["status"],
        "content_hash": result["content_hash"],
        "authorization_seal_content_hash": result["authorization_seal"]["content_hash"],
        "final_verification_content_hash": result["final_verification"]["content_hash"],
    }, indent=2, sort_keys=True))
    return 0 if result["status"] == READY_STATUS else 3


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())

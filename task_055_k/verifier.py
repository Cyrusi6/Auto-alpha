from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


EXPECTED_SCHEMA = "task055k_scrubbed_candidate_evidence_v1"
EXPECTED_STATUS = "task055k_single_canary_engineering_ready_waiting_operator_authorization_no_network_executed"
EXPECTED_CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
    "request_fingerprint": "8cec7ae0957a9d54afb1f08736db3f1c12b402554f5e1c3cc2e007658b8af869",
    "transport_identity": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_identity": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}
REQUIRED_ARTIFACT_ROLES = {
    "parent_verification",
    "source_seal",
    "candidate_authority",
    "native_rehearsal",
    "rehearsal_independent_verification",
    "final_report",
    "final_independent_verification",
    "candidate_checkpoint",
    "threat_model",
}
REQUIRED_APPLICATION_ROLES = {
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
}
ALLOWED_FIELDS = {
    "schema_version",
    "status",
    "implementation_commit",
    "parent_task055j_final_seal_hash",
    "ordered_exact_daily_keys",
    "ordered_key_root",
    "canary",
    "budgets",
    "source_entries",
    "source_root",
    "application_role_roots",
    "artifact_catalog",
    "artifact_catalog_root",
    "lineage",
    "broker_contract_hash",
    "threat_model",
    "network_authorized",
    "operator_authorization_required",
    "operational_state_unproven",
    "engineering_blockers",
    "certification_blockers",
    "certification_ready",
    "portfolio_ready",
    "optimizer_ready",
    "paper_ready",
    "live_ready",
    "network_execution",
    "contains_absolute_paths",
    "contains_market_values",
    "contains_credentials",
    "task055j_source_evidence_portable",
    "git_attestation_required",
    "content_hash",
}


class Task055KVerifierError(RuntimeError):
    pass


def verify_scrubbed_evidence(
    path: str | Path,
    *,
    repository_root: str | Path | None = None,
    require_git_attestation: bool = True,
) -> dict[str, Any]:
    evidence_path = Path(path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if set(payload) != ALLOWED_FIELDS:
        raise Task055KVerifierError("task055k_evidence_schema_field_set_invalid")
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if _hash(semantic) != payload.get("content_hash") or payload.get("schema_version") != EXPECTED_SCHEMA:
        raise Task055KVerifierError("task055k_evidence_content_or_schema_invalid")
    if payload.get("status") != EXPECTED_STATUS or payload.get("engineering_blockers"):
        raise Task055KVerifierError("task055k_evidence_status_or_blockers_invalid")
    ordered = list(payload.get("ordered_exact_daily_keys") or ())
    if len(ordered) != 17 or [row.get("ordinal") for row in ordered] != list(range(1, 18)):
        raise Task055KVerifierError("task055k_evidence_exact17_invalid")
    if ordered[0] != {"ordinal": 1, **EXPECTED_CANARY} or payload.get("canary") != EXPECTED_CANARY:
        raise Task055KVerifierError("task055k_evidence_first_canary_invalid")
    for row in ordered:
        _validate_key(row)
    if _hash(ordered) != payload.get("ordered_key_root"):
        raise Task055KVerifierError("task055k_evidence_ordered_key_root_invalid")
    for identity in ("request_fingerprint", "transport_identity", "evidence_use_identity"):
        if len({row[identity] for row in ordered}) != 17:
            raise Task055KVerifierError(f"task055k_evidence_identity_duplicate:{identity}")
    budgets = payload.get("budgets") or {}
    if budgets != {
        "unique_security_dates": 17,
        "logical_requests": 17,
        "physical_attempts": 0,
        "limits": {"unique_security_dates": 64, "logical_requests": 128, "physical_attempts": 160},
    }:
        raise Task055KVerifierError("task055k_evidence_budget_invalid")
    entries = list(payload.get("source_entries") or ())
    if not entries or _hash(entries) != payload.get("source_root"):
        raise Task055KVerifierError("task055k_evidence_source_root_invalid")
    for row in entries:
        if set(row) != {"path", "git_blob_id", "git_index_mode", "sha256", "size_bytes"}:
            raise Task055KVerifierError("task055k_evidence_source_entry_fields_invalid")
        if row["git_index_mode"] not in {"100644", "100755"} or not _hex(row["git_blob_id"], 40) or not _hex(row["sha256"], 64):
            raise Task055KVerifierError("task055k_evidence_source_entry_identity_invalid")
    role_roots = payload.get("application_role_roots") or {}
    if set(role_roots) != REQUIRED_APPLICATION_ROLES or not all(_hex(value, 64) for value in role_roots.values()):
        raise Task055KVerifierError("task055k_evidence_application_roles_invalid")
    catalog = list(payload.get("artifact_catalog") or ())
    if {row.get("role") for row in catalog} != REQUIRED_ARTIFACT_ROLES or _hash(catalog) != payload.get("artifact_catalog_root"):
        raise Task055KVerifierError("task055k_evidence_artifact_catalog_invalid")
    if any(set(row) != {"role", "sha256", "content_hash"} or not _hex(row["sha256"], 64) or not _hex(row["content_hash"], 64) for row in catalog):
        raise Task055KVerifierError("task055k_evidence_artifact_entry_invalid")
    if not payload.get("threat_model") == {
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
    }:
        raise Task055KVerifierError("task055k_evidence_threat_model_invalid")
    if payload.get("network_authorized") is not False or payload.get("operator_authorization_required") is not True:
        raise Task055KVerifierError("task055k_evidence_network_authorization_boundary_invalid")
    if payload.get("operational_state_unproven") is not True:
        raise Task055KVerifierError("task055k_evidence_operational_flag_invalid")
    if any(payload.get(key) is not False for key in ("certification_ready", "portfolio_ready", "optimizer_ready", "paper_ready", "live_ready")):
        raise Task055KVerifierError("task055k_evidence_downstream_readiness_invalid")
    if any(payload.get(key) is not False for key in ("contains_absolute_paths", "contains_market_values", "contains_credentials")):
        raise Task055KVerifierError("task055k_evidence_contains_flags_invalid")
    counters = payload.get("network_execution") or {}
    if counters != {
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "gpu_job_count": 0,
        "prospective_holdout_accessed": False,
        "max_read_date": "20260630",
    }:
        raise Task055KVerifierError("task055k_evidence_offline_counters_invalid")
    if payload.get("task055j_source_evidence_portable") is not False or payload.get("git_attestation_required") is not True:
        raise Task055KVerifierError("task055k_evidence_portability_boundary_invalid")
    encoded = json.dumps(payload, sort_keys=True)
    if any(value in encoded for value in ("/home/", "TUSHARE_TOKEN", "credential_file", '"open":')):
        raise Task055KVerifierError("task055k_evidence_forbidden_content")
    if repository_root is not None:
        _verify_repository(payload, evidence_path, Path(repository_root).resolve(), require_git_attestation)
    return payload | {"verified": True, "verification_hash": _hash(payload)}


def _verify_repository(payload: dict[str, Any], evidence_path: Path, repository: Path, require_attestation: bool) -> None:
    implementation = str(payload["implementation_commit"])
    _git(repository, "cat-file", "-e", f"{implementation}^{{commit}}")
    head = _git(repository, "rev-parse", "HEAD")
    if subprocess.run(["git", "merge-base", "--is-ancestor", implementation, head], cwd=repository).returncode:
        raise Task055KVerifierError("task055k_implementation_not_head_ancestor")
    changed = _git(repository, "diff", "--name-only", f"{implementation}..{head}").splitlines()
    if any(name not in {"README.md", "CATREADME.md", "FRAMEWORK_UPDATE.md"} and not name.startswith("evidence/task_055_k/") for name in changed):
        raise Task055KVerifierError("task055k_post_implementation_runtime_drift")
    expected = []
    for row in payload["source_entries"]:
        blob = _git(repository, "rev-parse", f"{implementation}:{row['path']}")
        content = subprocess.run(["git", "cat-file", "blob", blob], cwd=repository, check=True, capture_output=True).stdout
        tree = _git(repository, "ls-tree", implementation, row["path"]).split()
        expected.append(
            {
                "path": row["path"],
                "git_blob_id": blob,
                "git_index_mode": tree[0],
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    if expected != payload["source_entries"]:
        raise Task055KVerifierError("task055k_repository_source_entries_mismatch")
    if require_attestation:
        attestation_path = evidence_path.with_name("git_attestation.json")
        if not attestation_path.is_file():
            raise Task055KVerifierError("task055k_git_attestation_missing")
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        unsigned = {key: value for key, value in attestation.items() if key != "content_hash"}
        relative = evidence_path.resolve().relative_to(repository).as_posix()
        actual_blob = _git(repository, "rev-parse", f"HEAD:{relative}")
        if (
            _hash(unsigned) != attestation.get("content_hash")
            or attestation.get("schema_version") != "task055k_git_evidence_attestation_v1"
            or attestation.get("evidence_path") != relative
            or attestation.get("evidence_git_blob_id") != actual_blob
            or attestation.get("implementation_commit") != implementation
        ):
            raise Task055KVerifierError("task055k_git_attestation_invalid")


def _validate_key(row: dict[str, Any]) -> None:
    required = {"ordinal", "api_name", "ts_code", "trade_date", "fields", "request_fingerprint", "transport_identity", "evidence_use_identity"}
    if set(row) != required or row["api_name"] != "daily" or row["trade_date"] > "20260630":
        raise Task055KVerifierError("task055k_evidence_key_fields_invalid")
    normalized = {
        "version": "tushare_request.v1",
        "api_name": row["api_name"].strip(),
        "params": {"trade_date": row["trade_date"].strip(), "ts_code": row["ts_code"].strip()},
        "fields": list(dict.fromkeys(str(value).strip() for value in row["fields"] if str(value).strip())),
    }
    if _hash(normalized) != row["request_fingerprint"]:
        raise Task055KVerifierError("task055k_evidence_request_fingerprint_invalid")
    if not _hex(row["transport_identity"], 64) or not _hex(row["evidence_use_identity"], 64):
        raise Task055KVerifierError("task055k_evidence_transport_or_use_identity_invalid")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _hex(value: Any, length: int) -> bool:
    text = str(value or "")
    return len(text) == length and all(character in "0123456789abcdef" for character in text)


def _git(repository: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise Task055KVerifierError("task055k_git_verification_failed") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone Task055-K candidate evidence verifier")
    parser.add_argument("path")
    parser.add_argument("--repository-root")
    args = parser.parse_args(argv)
    try:
        result = verify_scrubbed_evidence(args.path, repository_root=args.repository_root)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"status": "passed", "verification_hash": result["verification_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

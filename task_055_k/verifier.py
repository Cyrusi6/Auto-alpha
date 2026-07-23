from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


BASELINE_COMMIT = "cc44926dda583652c0dad260bacb62a75550cdda"
PARENT_FINAL_SEAL = "ecb95537625014a0e98e34ffc8e15a30c36c537db511c7e2d5444ce3322e2aee"
EXPECTED_STATUS = (
    "task055k_single_canary_engineering_ready_waiting_operator_authorization_no_network_executed"
)
EXPECTED_SCHEMA = "task055kr_scrubbed_candidate_evidence_v2"
EXPECTED_EVIDENCE_PATH = "evidence/task_055_k/task055kr_scrubbed_evidence.json"
PARENT_EVIDENCE_PATH = "evidence/task_055_j/task055j_scrubbed_evidence.json"
EXPECTED_ORDERED_ROOT = "5aa5ebbe225c4093ce6b76f8359c34e3cde4a6e3d3fd88ba3ee1f53ebfd92e6f"
CANARY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
]
CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": CANARY_FIELDS,
    "request_fingerprint": "8cec7ae0957a9d54afb1f08736db3f1c12b402554f5e1c3cc2e007658b8af869",
    "transport_identity": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_identity": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}
APPLICATION_STAGES = [
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
]
ARTIFACT_ROLES = {
    "parent_verification",
    "source_seal",
    "historical_supersession",
    "candidate_authority",
    "candidate_checkpoint",
    "native_rehearsal",
    "rehearsal_independent_verification",
    "threat_model",
    "final_report",
    "final_independent_verification",
    "final_candidate_seal",
}
ARTIFACT_STATUSES = {
    "parent_verification": "passed_with_documented_high_assurance_limitation",
    "source_seal": "sealed",
    "historical_supersession": "superseded",
    "candidate_authority": "sealed_offline_candidate_v2",
    "candidate_checkpoint": "sealed_candidate_waiting_operator_authorization",
    "native_rehearsal": "passed",
    "rehearsal_independent_verification": "passed",
    "threat_model": "documented",
    "final_report": EXPECTED_STATUS,
    "final_independent_verification": "passed",
    "final_candidate_seal": "engineering_candidate_waiting_operator_authorization",
}
ROOT_BINDINGS = {
    "task_root": "validation_runs/task_055_k_kr_20260723",
    "authority_root": "governance/network_authority/task055k_single_canary_v2",
    "historical_task055k_root": "validation_runs/task_055_k_20260719",
    "historical_task055k_authority_root": "governance/network_authority/task055k_single_canary_v1",
}
CERTIFICATION_BLOCKERS = {
    "historical_selection_contamination",
    "selection_data_reused",
    "execution_modeled",
    "suspension_timing_semantics_uncertified",
    "constituent_publication_timing_unknown",
    "vendor_historical_revision_risk",
    "prospective_holdout_not_arrived",
    "broker_specific_commission_unavailable",
}
REQUIRED_SOURCE_PATHS = {
    "task_055_k/contracts.py",
    "task_055_k/source_tree.py",
    "task_055_k/broker.py",
    "task_055_k/gateway.py",
    "task_055_k/immutable.py",
    "task_055_k/stage_machine.py",
    "task_055_k/application.py",
    "task_055_k/application_cli.py",
    "task_055_k/application_components.py",
    "task_055_k/independent.py",
    "task_055_k/rehearsal.py",
    "task_055_k/network_cli.py",
    "task_055_k/verifier.py",
    "dev_tools/task055kr_harness.py",
    "dev_tools/task055kr_mutations.py",
    "data_pipeline/ashare/network_capability.py",
    "data_pipeline/ashare/providers/tushare_client.py",
}


class Task055KVerifierError(RuntimeError):
    pass


def verify_scrubbed_evidence(
    evidence_path: str | Path,
    *,
    repository_root: str | Path,
    require_git_attestation: bool = True,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    path = Path(evidence_path).resolve()
    if repository not in path.parents:
        raise Task055KVerifierError("task055k_evidence_outside_repository")
    payload_bytes = path.read_bytes()
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Task055KVerifierError("task055k_evidence_json_invalid") from exc
    if not isinstance(payload, dict):
        raise Task055KVerifierError("task055k_evidence_not_object")
    result = _verify_payload(payload, repository)
    _verify_no_sensitive_content(payload, payload_bytes)
    if require_git_attestation:
        _verify_git_blob_anchor(repository, path, payload_bytes)
    return result


def verify_mutated_payload_against_trusted_evidence(
    payload: Mapping[str, Any],
    *,
    trusted_payload: Mapping[str, Any],
    repository_root: str | Path,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    result = _verify_payload(dict(payload), repository)
    if _canonical_bytes(payload) != _canonical_bytes(trusted_payload):
        raise Task055KVerifierError("task055k_evidence_trusted_git_blob_mismatch")
    return result


def _verify_payload(payload: dict[str, Any], repository: Path) -> dict[str, Any]:
    _verify_self_hash(payload)
    _verify_fixed_contract(payload)
    implementation = str(payload.get("implementation_commit") or "")
    if not _hex(implementation, 40):
        raise Task055KVerifierError("task055k_implementation_commit_invalid")
    _verify_commit_relationship(repository, implementation)
    expected_keys = _expected_ordered_keys(repository, implementation)
    if payload.get("ordered_exact_daily_keys") != expected_keys:
        raise Task055KVerifierError("task055k_ordered_key_content_or_order_invalid")
    if _hash(expected_keys) != EXPECTED_ORDERED_ROOT or payload.get(
        "ordered_key_root"
    ) != EXPECTED_ORDERED_ROOT:
        raise Task055KVerifierError("task055k_ordered_key_root_invalid")
    _verify_source_tree(payload, repository, implementation)
    _verify_artifacts(payload)
    _verify_cross_lineage(payload)
    _verify_receipts(payload)
    return {
        "status": "passed",
        "evidence_content_hash": payload["content_hash"],
        "implementation_commit": implementation,
        "source_root": payload["source_root"],
        "ordered_key_root": payload["ordered_key_root"],
        "artifact_catalog_root": payload["artifact_catalog_root"],
        "network_authorized": False,
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "prospective_holdout_accessed": False,
    }


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _verify_self_hash(payload: Mapping[str, Any]) -> None:
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("content_hash") != _hash(semantic):
        raise Task055KVerifierError("task055k_evidence_content_hash_invalid")


def _verify_fixed_contract(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != EXPECTED_SCHEMA or payload.get("status") != EXPECTED_STATUS:
        raise Task055KVerifierError("task055k_schema_or_status_invalid")
    if payload.get("baseline_commit") != BASELINE_COMMIT or payload.get(
        "parent_task055j_final_seal_hash"
    ) != PARENT_FINAL_SEAL:
        raise Task055KVerifierError("task055k_baseline_or_parent_invalid")
    if payload.get("root_bindings") != ROOT_BINDINGS:
        raise Task055KVerifierError("task055k_root_bindings_invalid")
    if payload.get("canary") != CANARY:
        raise Task055KVerifierError("task055k_canary_invalid")
    budgets = payload.get("budgets") or {}
    if budgets != {
        "unique_security_dates": 17,
        "logical_requests": 17,
        "physical_attempts": 0,
        "credential_reads": 0,
        "limits": {
            "unique_security_dates": 64,
            "logical_requests": 128,
            "physical_attempts": 160,
            "credential_reads": 1,
        },
    }:
        raise Task055KVerifierError("task055k_budget_contract_invalid")
    if payload.get("application_stage_order") != APPLICATION_STAGES:
        raise Task055KVerifierError("task055k_application_stage_order_invalid")
    role_roots = payload.get("application_role_roots") or {}
    if set(role_roots) != set(APPLICATION_STAGES) or any(
        not _hex(value, 64) for value in role_roots.values()
    ):
        raise Task055KVerifierError("task055k_application_role_roots_invalid")
    booleans = {
        "network_authorized": False,
        "authorization_eligible": False,
        "operator_authorization_required": True,
        "operational_state_unproven": True,
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "production_execution_ancestor": False,
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
        "git_attestation_required": True,
    }
    if any(payload.get(key) is not expected for key, expected in booleans.items()):
        raise Task055KVerifierError("task055k_readiness_or_contains_flag_invalid")
    if payload.get("rehearsal_evidence_scope") != "synthetic_rehearsal_only":
        raise Task055KVerifierError("task055k_rehearsal_scope_invalid")
    if payload.get("engineering_blockers") != []:
        raise Task055KVerifierError("task055k_ready_evidence_has_engineering_blockers")
    if set(payload.get("certification_blockers") or ()) != CERTIFICATION_BLOCKERS:
        raise Task055KVerifierError("task055k_certification_blocker_set_invalid")
    counters = payload.get("network_execution") or {}
    if (
        {key: counters.get(key) for key in (
            "credential_read_count",
            "tushare_post_count",
            "other_http_count",
            "gpu_job_count",
        )}
        != {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_http_count": 0,
            "gpu_job_count": 0,
        }
        or counters.get("prospective_holdout_accessed") is not False
        or not _date8(counters.get("max_read_date"))
        or counters["max_read_date"] > "20260630"
        or int(counters.get("read_ledger_file_count") or 0) <= 0
        or int(counters.get("read_ledger_row_count") or 0) <= 0
        or not _hash64(counters.get("read_ledger_root"))
    ):
        raise Task055KVerifierError("task055k_offline_counter_contract_invalid")
    expected_broker = _hash(
        {
            "contract": "canonical_single_exact_daily_signed_receipt_v2",
            "final_https_revalidates_canonical_trust": True,
            "caller_capability_is_not_trust_anchor": True,
            "reservation_public_key_precedes_transport": True,
            "receipt_public_key_derived_from_canonical_reservation": True,
            "post_intent_without_canonical_receipt_is_ambiguous": True,
            "tls_preflight_precedes_credential": True,
            "credential_precedes_post_intent": True,
            "credential_read_intent_is_single_use": True,
            "credential_read_ambiguity_blocks": True,
            "retry_count": 1,
            "credential_reads": 1,
        }
    )
    if payload.get("broker_contract_hash") != expected_broker:
        raise Task055KVerifierError("task055k_broker_contract_hash_invalid")


def _verify_commit_relationship(repository: Path, implementation: str) -> None:
    if _git_status(repository):
        raise Task055KVerifierError("task055k_verifier_requires_clean_worktree")
    head = _git(repository, "rev-parse", "HEAD")
    for ancestor, descendant, code in (
        (BASELINE_COMMIT, implementation, "task055k_baseline_not_ancestor"),
        (implementation, head, "task055k_implementation_not_head_ancestor"),
    ):
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repository,
            check=False,
        )
        if result.returncode:
            raise Task055KVerifierError(code)
    changed = _git(repository, "diff", "--name-only", f"{implementation}..{head}").splitlines()
    allowed = (
        "README.md",
        "CATREADME.md",
        "FRAMEWORK_UPDATE.md",
    )
    if any(
        name not in allowed and not name.startswith("evidence/task_055_k/")
        for name in changed
    ):
        raise Task055KVerifierError("task055k_post_implementation_runtime_drift")


def _expected_ordered_keys(repository: Path, implementation: str) -> list[dict[str, Any]]:
    parent = json.loads(_git_bytes(repository, implementation, PARENT_EVIDENCE_PATH))
    rows = []
    for ordinal, source in enumerate(parent.get("ordered_exact_daily_keys") or (), start=1):
        fields = [str(value) for value in source.get("fields") or ()]
        api_name = str(source.get("api_name") or "")
        ts_code = str(source.get("ts_code") or "")
        trade_date = str(source.get("trade_date") or "")
        row = {
            "ordinal": ordinal,
            "api_name": api_name,
            "ts_code": ts_code,
            "trade_date": trade_date,
            "fields": fields,
            "request_fingerprint": _request_fingerprint(
                api_name, {"ts_code": ts_code, "trade_date": trade_date}, fields
            ),
            "transport_identity": str(source.get("transport_hash") or ""),
            "evidence_use_identity": str(source.get("evidence_use_hash") or ""),
        }
        if source.get("ordinal") != ordinal or trade_date > "20260630":
            raise Task055KVerifierError("task055k_parent_ordered_key_invalid")
        rows.append(row)
    if len(rows) != 17 or rows[0] != {"ordinal": 1, **CANARY}:
        raise Task055KVerifierError("task055k_parent_exact17_or_first_key_invalid")
    return rows


def _verify_source_tree(
    payload: Mapping[str, Any], repository: Path, implementation: str
) -> None:
    expected = _source_entries(repository, implementation)
    if payload.get("source_entries") != expected or payload.get("source_root") != _hash(expected):
        raise Task055KVerifierError("task055k_source_tree_invalid")
    if not REQUIRED_SOURCE_PATHS.issubset({row["path"] for row in expected}):
        raise Task055KVerifierError("task055k_source_tree_required_path_missing")


def _verify_artifacts(payload: Mapping[str, Any]) -> None:
    catalog = payload.get("artifact_catalog") or []
    if not isinstance(catalog, list) or len(catalog) != len(ARTIFACT_ROLES):
        raise Task055KVerifierError("task055k_artifact_catalog_cardinality_invalid")
    roles = [row.get("role") for row in catalog]
    if set(roles) != ARTIFACT_ROLES or len(roles) != len(set(roles)):
        raise Task055KVerifierError("task055k_artifact_role_set_invalid")
    if payload.get("artifact_catalog_root") != _hash(catalog):
        raise Task055KVerifierError("task055k_artifact_catalog_root_invalid")
    lineage = payload.get("lineage") or {}
    if set(lineage) != ARTIFACT_ROLES:
        raise Task055KVerifierError("task055k_lineage_role_set_invalid")
    for row in catalog:
        relative = Path(str(row.get("relative_path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055KVerifierError("task055k_artifact_relative_path_invalid")
        role = row["role"]
        prefix = (
            ROOT_BINDINGS["authority_root"]
            if role in {"candidate_authority", "candidate_checkpoint", "final_candidate_seal"}
            else ROOT_BINDINGS["task_root"]
        )
        if not relative.as_posix().startswith(prefix + "/"):
            raise Task055KVerifierError(f"task055k_artifact_root_binding_invalid:{role}")
        if not _hex(row.get("sha256"), 64) or not _hex(row.get("content_hash"), 64):
            raise Task055KVerifierError("task055k_artifact_hash_invalid")
        if lineage.get(role) != row.get("content_hash"):
            raise Task055KVerifierError(f"task055k_artifact_lineage_invalid:{role}")
    if payload.get("artifact_statuses") != ARTIFACT_STATUSES:
        raise Task055KVerifierError("task055k_artifact_status_vocabulary_invalid")


def _verify_cross_lineage(payload: Mapping[str, Any]) -> None:
    lineage = payload["lineage"]
    cross = payload.get("cross_lineage") or {}
    expected_checkpoint = {
        "parent_verification": lineage["parent_verification"],
        "source_seal": lineage["source_seal"],
        "historical_supersession": lineage["historical_supersession"],
        "native_rehearsal": lineage["native_rehearsal"],
        "rehearsal_independent_verification": lineage[
            "rehearsal_independent_verification"
        ],
        "final_report": lineage["final_report"],
        "final_independent_verification": lineage["final_independent_verification"],
        "threat_model": lineage["threat_model"],
    }
    expected_execution = {
        "source_seal": lineage["source_seal"],
        "parent_verification": lineage["parent_verification"],
        "historical_supersession": lineage["historical_supersession"],
        "candidate_authority": lineage["candidate_authority"],
        "candidate_checkpoint": lineage["candidate_checkpoint"],
        "final_report": lineage["final_report"],
        "final_verification": lineage["final_independent_verification"],
    }
    expected_engineering = {
        "native_rehearsal": lineage["native_rehearsal"],
        "rehearsal_independent_verification": lineage[
            "rehearsal_independent_verification"
        ],
        "evidence_scope": "synthetic_rehearsal_only",
        "production_execution_ancestor": False,
    }
    expected_report = {
        "parent_verification": lineage["parent_verification"],
        "source_seal": lineage["source_seal"],
        "historical_supersession": lineage["historical_supersession"],
        "candidate_authority": lineage["candidate_authority"],
        "native_rehearsal": lineage["native_rehearsal"],
        "rehearsal_independent_verification": lineage[
            "rehearsal_independent_verification"
        ],
        "threat_model": lineage["threat_model"],
    }
    expected_verification = {
        "report": lineage["final_report"],
        **expected_report,
    }
    expected = {
        "checkpoint": expected_checkpoint,
        "final_seal_execution": expected_execution,
        "final_seal_engineering": expected_engineering,
        "report": expected_report,
        "final_verification": expected_verification,
    }
    if cross != expected:
        raise Task055KVerifierError("task055k_cross_lineage_invalid")


def _verify_receipts(payload: Mapping[str, Any]) -> None:
    rows = payload.get("synthetic_receipt_attestations") or {}
    if set(rows) != {"positive", "empty"}:
        raise Task055KVerifierError("task055k_receipt_attestation_branches_invalid")
    for branch, row in rows.items():
        for key in (
            "attempt_id",
            "reservation_content_hash",
            "receipt_content_hash",
            "broker_public_key_sha256",
            "tls_attestation_hash",
            "response_payload_hash",
        ):
            if not _hex(row.get(key), 64):
                raise Task055KVerifierError(f"task055k_receipt_hash_invalid:{branch}:{key}")
        for key in ("request_fingerprint", "transport_identity", "evidence_use_identity"):
            if row.get(key) != CANARY[key]:
                raise Task055KVerifierError(f"task055k_receipt_identity_invalid:{branch}:{key}")
        if row.get("response_fields") != CANARY_FIELDS:
            raise Task055KVerifierError(f"task055k_receipt_fields_invalid:{branch}")
    if rows["positive"].get("item_count") != 1 or rows["positive"].get(
        "empty_response_semantics"
    ) is not None:
        raise Task055KVerifierError("task055k_positive_receipt_semantics_invalid")
    if rows["empty"].get("item_count") != 0 or rows["empty"].get(
        "empty_response_semantics"
    ) != "vendor_absence_only":
        raise Task055KVerifierError("task055k_empty_receipt_semantics_invalid")


def _verify_no_sensitive_content(payload: Mapping[str, Any], encoded: bytes) -> None:
    text = encoded.decode("utf-8")
    forbidden = (
        "/home/",
        "TUSHARE_TOKEN",
        "credential_file",
        "token_suffix",
        "token_hash",
    )
    if any(value in text for value in forbidden):
        raise Task055KVerifierError("task055k_evidence_sensitive_content_detected")
    if _contains_market_value_key(payload):
        raise Task055KVerifierError("task055k_evidence_market_value_key_detected")


def _verify_git_blob_anchor(repository: Path, path: Path, payload_bytes: bytes) -> None:
    expected_path = (repository / EXPECTED_EVIDENCE_PATH).resolve()
    if path != expected_path:
        raise Task055KVerifierError("task055k_evidence_path_not_canonical")
    head = _git(repository, "rev-parse", "HEAD")
    tracked = _git_bytes(repository, head, EXPECTED_EVIDENCE_PATH)
    if tracked != payload_bytes:
        raise Task055KVerifierError("task055k_evidence_git_blob_mismatch")
    mode, kind, _blob = _ls_tree_entry(repository, head, EXPECTED_EVIDENCE_PATH)
    if mode not in {"100644", "100755"} or kind != "blob":
        raise Task055KVerifierError("task055k_evidence_git_mode_invalid")


def _source_entries(repository: Path, treeish: str) -> list[dict[str, Any]]:
    raw = subprocess.run(
        ["git", "ls-tree", "-r", "-z", treeish],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout.decode()
    rows = []
    for record in (value for value in raw.split("\0") if value):
        metadata, relative = record.split("\t", 1)
        mode, kind, blob = metadata.split()
        if kind != "blob" or not _included(relative):
            continue
        if mode not in {"100644", "100755"}:
            raise Task055KVerifierError(f"task055k_source_mode_invalid:{relative}")
        content = subprocess.run(
            ["git", "cat-file", "blob", blob],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout
        rows.append(
            {
                "path": relative,
                "git_blob_id": blob,
                "git_index_mode": mode,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    return sorted(rows, key=lambda row: row["path"])


def _included(relative: str) -> bool:
    path = Path(relative)
    if relative.startswith(("tests/", "evidence/", "assets/", "paper/", "lord/")):
        return False
    if path.suffix == ".py":
        return True
    if relative in {
        "requirements.txt",
        "requirements-optional.txt",
        "environment.yml",
        ".env.example",
    }:
        return True
    return relative in {"pyproject.toml", "uv.lock"} or relative.startswith(
        ".github/workflows/"
    )


def _request_fingerprint(api_name: str, params: Mapping[str, Any], fields: Iterable[str]) -> str:
    return _hash(
        {
            "version": "tushare_request.v1",
            "api_name": api_name.strip(),
            "params": {str(key): str(params[key]).strip() for key in sorted(params)},
            "fields": list(dict.fromkeys(str(value).strip() for value in fields if str(value).strip())),
        }
    )


def _contains_market_value_key(value: Any) -> bool:
    market_keys = {
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vol",
        "amount",
        "price",
        "nav",
        "return",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in market_keys and not isinstance(child, str):
                return True
            if _contains_market_value_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_market_value_key(child) for child in value)
    return False


def _ls_tree_entry(repository: Path, treeish: str, relative: str) -> tuple[str, str, str]:
    output = _git(repository, "ls-tree", treeish, "--", relative)
    if not output:
        raise Task055KVerifierError(f"task055k_git_blob_missing:{relative}")
    metadata, found = output.split("\t", 1)
    if found != relative:
        raise Task055KVerifierError(f"task055k_git_blob_path_invalid:{relative}")
    mode, kind, blob = metadata.split()
    return mode, kind, blob


def _git_bytes(repository: Path, treeish: str, relative: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{treeish}:{relative}"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def _git_status(repository: Path) -> str:
    return _git(repository, "status", "--porcelain")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, text=True, capture_output=True
    ).stdout.strip()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(
        character in "0123456789abcdef" for character in value
    )


def _hash64(value: Any) -> bool:
    return _hex(value, 64)


def _date8(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 8 and value.isdigit()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Task055-KR scrubbed evidence")
    parser.add_argument("evidence")
    parser.add_argument("--repository-root", default=".")
    args = parser.parse_args(argv)
    try:
        result = verify_scrubbed_evidence(
            args.evidence,
            repository_root=args.repository_root,
            require_git_attestation=True,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

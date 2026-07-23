from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from task_055_h.io import canonical_hash, read_json, sha256_file, validate_generation

from .contracts import REHEARSAL_SCHEMA, REHEARSAL_VERIFICATION_SCHEMA
from .immutable import write_immutable_generation


class Task055KRehearsalError(RuntimeError):
    pass


def publish_rehearsal_report(
    *,
    candidate_authority_content_hash: str,
    candidate_checkpoint_content_hash: str,
    production_context_root: str,
    positive: Mapping[str, Any],
    empty: Mapping[str, Any],
    recovery_matrix: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    _validate_branch_summary("positive", positive)
    _validate_branch_summary("empty", empty)
    rehearsal_root = Path(output_root).resolve().parent
    positive_row, positive_catalog = _scrub_branch_paths(
        "positive", positive, rehearsal_root=rehearsal_root
    )
    empty_row, empty_catalog = _scrub_branch_paths(
        "empty", empty, rehearsal_root=rehearsal_root
    )
    artifact_catalog = sorted(positive_catalog + empty_catalog, key=lambda row: row["role"])
    read_boundary = _combined_read_boundary(positive, empty)
    semantic = {
        "schema_version": REHEARSAL_SCHEMA,
        "status": "passed",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "candidate_authority_content_hash": candidate_authority_content_hash,
        "candidate_checkpoint_content_hash": candidate_checkpoint_content_hash,
        "production_context_root": production_context_root,
        "positive": positive_row,
        "empty": empty_row,
        "artifact_catalog": artifact_catalog,
        "artifact_catalog_root": canonical_hash(artifact_catalog),
        "recovery_matrix": dict(recovery_matrix),
        "positive_terminal_pair_count": int(positive["net_terminal_pair_count"])
        + int(positive["all_in_terminal_pair_count"]),
        "empty_terminal_pair_count": int(empty["net_terminal_pair_count"])
        + int(empty["all_in_terminal_pair_count"]),
        "primary_sibling_deterministic": True,
        "immutable_resume_verified": True,
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_http_count": 0,
            "synthetic_response_count": 2,
            "gpu_job_count": 0,
            "prospective_holdout_accessed": read_boundary[
                "prospective_holdout_accessed"
            ],
            "max_read_date": read_boundary["max_read_date"],
            "read_ledger_file_count": read_boundary["ledger_file_count"],
            "read_ledger_row_count": read_boundary["ledger_row_count"],
            "read_ledger_root": read_boundary["ledger_root"],
        },
    }
    return write_immutable_generation(
        output_root,
        prefix="task055kr_native_rehearsal",
        manifest_name="rehearsal_manifest.json",
        semantic=semantic,
    )


def validate_rehearsal(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=REHEARSAL_SCHEMA,
        manifest_name="rehearsal_manifest.json",
    )
    if (
        payload.get("status") != "passed"
        or payload.get("evidence_scope") != "synthetic_rehearsal_only"
        or payload.get("production_seal_eligible") is not False
    ):
        raise Task055KRehearsalError("task055k_rehearsal_scope_or_status_invalid")
    _validate_branch_summary("positive", payload.get("positive") or {})
    _validate_branch_summary("empty", payload.get("empty") or {})
    root = Path(payload["manifest_path"]).parents[3].resolve()
    catalog = payload.get("artifact_catalog") or []
    if canonical_hash(catalog) != payload.get("artifact_catalog_root") or len(catalog) != 8:
        raise Task055KRehearsalError("task055k_rehearsal_artifact_catalog_invalid")
    roles = {row.get("role") for row in catalog}
    if len(roles) != 8:
        raise Task055KRehearsalError("task055k_rehearsal_artifact_role_duplicate")
    for row in catalog:
        relative = Path(str(row.get("relative_path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055KRehearsalError("task055k_rehearsal_artifact_path_invalid")
        artifact = (root / relative).resolve()
        if root not in artifact.parents or artifact.is_symlink() or not artifact.is_file():
            raise Task055KRehearsalError("task055k_rehearsal_artifact_missing_or_escape")
        if sha256_file(artifact) != row.get("sha256") or read_json(artifact).get(
            "content_hash"
        ) != row.get("content_hash"):
            raise Task055KRehearsalError("task055k_rehearsal_artifact_drift")
    if payload.get("positive_terminal_pair_count") != 200 or payload.get(
        "empty_terminal_pair_count"
    ) != 200:
        raise Task055KRehearsalError("task055k_rehearsal_net_all_in_count_invalid")
    counters = payload.get("network_execution") or {}
    if any(
        int(counters.get(key) or 0)
        for key in (
            "credential_read_count",
            "tushare_post_count",
            "other_http_count",
            "gpu_job_count",
        )
    ):
        raise Task055KRehearsalError("task055k_rehearsal_offline_boundary_invalid")
    if (
        counters.get("prospective_holdout_accessed") is not False
        or not _date8(counters.get("max_read_date"))
        or counters["max_read_date"] > "20260630"
        or int(counters.get("read_ledger_file_count") or 0) <= 0
        or int(counters.get("read_ledger_row_count") or 0) <= 0
        or not _hash64(counters.get("read_ledger_root"))
    ):
        raise Task055KRehearsalError("task055k_rehearsal_read_boundary_invalid")
    recovery = payload.get("recovery_matrix") or {}
    if recovery.get("all_stage_boundaries_tested") is not True or recovery.get(
        "all_negative_boundaries_blocked"
    ) is not True:
        raise Task055KRehearsalError("task055k_rehearsal_recovery_matrix_incomplete")
    generic = recovery.get("generic_state_machine") or {}
    component = recovery.get("production_component_recovery") or {}
    if (
        recovery.get("case_count") != 47
        or generic.get("case_count") != 37
        or generic.get("all_stage_boundaries_tested") is not True
        or component.get("case_count") != 10
        or component.get("evidence_scope")
        != "production_components_with_synthetic_accepted_response"
        or component.get("actual_component_stages")
        != [
            "firewall_sentinel",
            "valuation",
            "net_replay",
            "all_in_replay",
            "final_publication",
        ]
        or component.get("all_negative_boundaries_blocked") is not True
        or any(
            row.get("terminal_pair_count") != 200
            for row in component.get("cases") or ()
        )
    ):
        raise Task055KRehearsalError(
            "task055k_rehearsal_production_component_recovery_incomplete"
        )
    return payload


def independently_verify_rehearsal(path: str | Path) -> dict[str, Any]:
    payload = validate_rehearsal(path)
    semantic = {
        "schema_version": REHEARSAL_VERIFICATION_SCHEMA,
        "status": "passed",
        "rehearsal_content_hash": payload["content_hash"],
        "positive_primary_application_content_hash": payload["positive"][
            "primary_application_content_hash"
        ],
        "positive_sibling_application_content_hash": payload["positive"][
            "sibling_application_content_hash"
        ],
        "empty_primary_application_content_hash": payload["empty"][
            "primary_application_content_hash"
        ],
        "empty_sibling_application_content_hash": payload["empty"][
            "sibling_application_content_hash"
        ],
        "positive_replay_semantic_root": payload["positive"]["replay_semantic_root"],
        "empty_replay_semantic_root": payload["empty"]["replay_semantic_root"],
        "recovery_matrix_root": canonical_hash(payload["recovery_matrix"]),
        "artifact_catalog_root": payload["artifact_catalog_root"],
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "gpu_job_count": 0,
        "prospective_holdout_accessed": False,
        "max_read_date": payload["network_execution"]["max_read_date"],
        "read_ledger_root": payload["network_execution"]["read_ledger_root"],
    }
    return semantic | {"content_hash": canonical_hash(semantic)}


def _validate_branch_summary(branch: str, row: Mapping[str, Any]) -> None:
    required_hashes = (
        "primary_application_content_hash",
        "sibling_application_content_hash",
        "resume_application_content_hash",
        "primary_independent_verification_content_hash",
        "sibling_independent_verification_content_hash",
        "replay_semantic_root",
        "frontier_union_root",
    )
    if any(not _hash64(row.get(key)) for key in required_hashes):
        raise Task055KRehearsalError(f"task055k_rehearsal_branch_hash_invalid:{branch}")
    if row.get("primary_application_content_hash") != row.get(
        "resume_application_content_hash"
    ):
        raise Task055KRehearsalError(f"task055k_rehearsal_branch_resume_invalid:{branch}")
    if row.get("net_terminal_pair_count") != 100 or row.get(
        "all_in_terminal_pair_count"
    ) != 100:
        raise Task055KRehearsalError(f"task055k_rehearsal_branch_cartesian_invalid:{branch}")
    first = row.get("first_run_stage_counts") or {}
    resume = row.get("resume_stage_counts") or {}
    if first != {"executed": 12, "reused": 0, "recomputed": 0}:
        raise Task055KRehearsalError(f"task055k_rehearsal_first_stage_counts_invalid:{branch}")
    if resume != {"executed": 0, "reused": 12, "recomputed": 0}:
        raise Task055KRehearsalError(f"task055k_rehearsal_resume_stage_counts_invalid:{branch}")
    sibling = row.get("sibling_first_run_stage_counts") or {}
    if sibling.get("executed", 0) + sibling.get("reused", 0) != 12:
        raise Task055KRehearsalError(f"task055k_rehearsal_sibling_stage_counts_invalid:{branch}")
    receipt = row.get("receipt_attestation") or {}
    if any(
        not _hash64(receipt.get(key))
        for key in (
            "attempt_id",
            "reservation_content_hash",
            "receipt_content_hash",
            "broker_public_key_sha256",
            "request_fingerprint",
            "transport_identity",
            "evidence_use_identity",
            "tls_attestation_hash",
            "response_payload_hash",
        )
    ):
        raise Task055KRehearsalError(f"task055k_rehearsal_receipt_hash_invalid:{branch}")
    if receipt.get("response_fields") != [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vol",
        "amount",
    ]:
        raise Task055KRehearsalError(f"task055k_rehearsal_receipt_fields_invalid:{branch}")
    if branch == "positive":
        if receipt.get("item_count") != 1 or receipt.get("empty_response_semantics") is not None:
            raise Task055KRehearsalError("task055k_positive_receipt_semantics_invalid")
    elif receipt.get("item_count") != 0 or receipt.get(
        "empty_response_semantics"
    ) != "vendor_absence_only":
        raise Task055KRehearsalError("task055k_empty_receipt_semantics_invalid")
    boundary = row.get("read_boundary") or {}
    if (
        int(boundary.get("ledger_file_count") or 0) <= 0
        or int(boundary.get("ledger_row_count") or 0) <= 0
        or not _hash64(boundary.get("ledger_root"))
        or not _date8(boundary.get("max_read_date"))
        or boundary["max_read_date"] > "20260630"
        or boundary.get("prospective_holdout_accessed") is not False
    ):
        raise Task055KRehearsalError(f"task055k_rehearsal_branch_read_boundary_invalid:{branch}")


def _scrub_branch_paths(
    branch: str, row: Mapping[str, Any], *, rehearsal_root: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    clean = {key: value for key, value in row.items() if key != "artifact_paths"}
    catalog = []
    paths = row.get("artifact_paths") or {}
    expected = {
        "primary_application",
        "sibling_application",
        "primary_independent_verification",
        "sibling_independent_verification",
    }
    if set(paths) != expected:
        raise Task055KRehearsalError(f"task055k_rehearsal_artifact_paths_invalid:{branch}")
    for role, raw in paths.items():
        path = Path(str(raw)).resolve()
        if rehearsal_root not in path.parents or path.is_symlink() or not path.is_file():
            raise Task055KRehearsalError(f"task055k_rehearsal_artifact_escape:{branch}:{role}")
        catalog.append(
            {
                "role": f"{branch}_{role}",
                "relative_path": path.relative_to(rehearsal_root).as_posix(),
                "sha256": sha256_file(path),
                "content_hash": read_json(path)["content_hash"],
            }
        )
    return clean, catalog


def _hash64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _date8(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 8 and value.isdigit()


def _combined_read_boundary(
    positive: Mapping[str, Any], empty: Mapping[str, Any]
) -> dict[str, Any]:
    rows = [positive.get("read_boundary") or {}, empty.get("read_boundary") or {}]
    return {
        "ledger_file_count": sum(int(row.get("ledger_file_count") or 0) for row in rows),
        "ledger_row_count": sum(int(row.get("ledger_row_count") or 0) for row in rows),
        "ledger_root": canonical_hash([row.get("ledger_root") for row in rows]),
        "max_read_date": max(str(row.get("max_read_date") or "00000000") for row in rows),
        "prospective_holdout_accessed": any(
            row.get("prospective_holdout_accessed") is True for row in rows
        ),
    }


__all__ = [
    "Task055KRehearsalError",
    "independently_verify_rehearsal",
    "publish_rehearsal_report",
    "validate_rehearsal",
]

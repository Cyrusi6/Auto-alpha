from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


BASELINE_COMMIT = "df24308eadab07128b9efead884355247e58a382"
CANDIDATE_STATUS = "task055kr2_candidate_ready_for_independent_audit_no_network_executed"
ANCHOR_SCHEMA = "task055kr2_external_release_candidate_anchor_v1"
ANCHOR_PATH = "evidence/task_055_k/task055kr2_candidate_anchor.json"
SUPERSESSION_PATH = "evidence/task_055_k/task055kr2_supersession.json"
CANDIDATE_EVIDENCE_PATH = "evidence/task_055_k/task055kr2_candidate_evidence.json"
EVIDENCE_COMMIT_ALLOWLIST = (
    "README.md",
    "CATREADME.md",
    "FRAMEWORK_UPDATE.md",
    CANDIDATE_EVIDENCE_PATH,
)
ANCHOR_COMMIT_ALLOWLIST = (ANCHOR_PATH, SUPERSESSION_PATH)


class Task055KR2ReleaseError(RuntimeError):
    pass


def build_candidate_anchor(
    *,
    repository_root: str | Path,
    implementation_commit: str,
    evidence_commit: str,
    governed_root: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    if _git(repository, "status", "--porcelain"):
        raise Task055KR2ReleaseError("task055kr2_anchor_requires_clean_worktree")
    head = _git(repository, "rev-parse", "HEAD")
    if head != evidence_commit:
        raise Task055KR2ReleaseError("task055kr2_anchor_requires_exact_evidence_commit")
    if not _is_ancestor(repository, BASELINE_COMMIT, implementation_commit):
        raise Task055KR2ReleaseError("task055kr2_implementation_baseline_invalid")
    if _single_parent(repository, evidence_commit) != implementation_commit:
        raise Task055KR2ReleaseError("task055kr2_evidence_parent_invalid")
    evidence_changes = _changed_paths(repository, implementation_commit, evidence_commit)
    if tuple(evidence_changes) != tuple(sorted(EVIDENCE_COMMIT_ALLOWLIST)):
        raise Task055KR2ReleaseError("task055kr2_evidence_commit_allowlist_invalid")
    evidence_path = repository / CANDIDATE_EVIDENCE_PATH
    evidence_bytes = evidence_path.read_bytes()
    evidence = _json_object(evidence_bytes, code="candidate_evidence")
    _validate_candidate_evidence(evidence)
    source_entries = _release_source_entries(repository, implementation_commit)
    source_root = _hash(source_entries)
    verifier = next(
        (row for row in source_entries if row["path"] == "src/auto_alpha/platform/network_authority/verifier.py"),
        None,
    )
    if verifier is None:
        raise Task055KR2ReleaseError("task055kr2_verifier_source_missing")
    top_catalog = _validate_artifact_catalog(
        evidence.get("artifact_catalog"), governed=governed
    )
    rehearsal, rehearsal_catalog, rehearsal_root = _load_rehearsal_release_catalog(
        governed=governed,
        top_catalog=top_catalog,
    )
    stage_roots, application_roots = _application_roots(
        governed=rehearsal_root,
        rehearsal_catalog=rehearsal_catalog,
    )
    public_keys = _reservation_public_keys(
        governed=rehearsal_root,
        rehearsal_catalog=rehearsal_catalog,
    )
    evidence_entry = _git_blob_entry(repository, evidence_commit, CANDIDATE_EVIDENCE_PATH)
    if evidence_entry["sha256"] != hashlib.sha256(evidence_bytes).hexdigest():
        raise Task055KR2ReleaseError("task055kr2_evidence_worktree_blob_mismatch")
    semantic_expectations = {
        "status": CANDIDATE_STATUS,
        "implementation_commit": implementation_commit,
        "baseline_commit": BASELINE_COMMIT,
        "ordered_exact_daily_keys": evidence["ordered_exact_daily_keys"],
        "ordered_key_root": evidence["ordered_key_root"],
        "canary": evidence["canary"],
        "budgets": evidence["budgets"],
        "root_bindings": evidence["root_bindings"],
        "source_entries": evidence["source_entries"],
        "source_root": evidence["source_root"],
        "artifact_catalog": top_catalog,
        "artifact_catalog_root": _hash(top_catalog),
        "lineage": evidence["lineage"],
        "cross_lineage": evidence["cross_lineage"],
        "application_stage_order": evidence["application_stage_order"],
        "application_role_roots": evidence["application_role_roots"],
        "synthetic_receipt_attestations": evidence[
            "synthetic_receipt_attestations"
        ],
        "broker_contract_hash": evidence["broker_contract_hash"],
        "network_authorized": False,
        "executable": False,
        "authorization_eligible": False,
        "operational_state_unproven": True,
        "contains_credentials": False,
        "contains_market_values": False,
        "contains_absolute_paths": False,
        "prospective_holdout_accessed": False,
        "production_execution_ancestor": False,
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    semantic = {
        "schema_version": ANCHOR_SCHEMA,
        "status": CANDIDATE_STATUS,
        "release_topology": {
            "baseline_commit": BASELINE_COMMIT,
            "implementation_commit": implementation_commit,
            "evidence_commit": evidence_commit,
            "anchor_parent_must_equal_evidence_commit": True,
            "implementation_parent_must_equal_baseline": False,
            "baseline_must_be_ancestor_of_implementation": True,
            "evidence_parent_must_equal_implementation": True,
            "evidence_commit_allowlist": list(EVIDENCE_COMMIT_ALLOWLIST),
            "anchor_commit_allowlist": list(ANCHOR_COMMIT_ALLOWLIST),
        },
        "source_entries": source_entries,
        "source_root": source_root,
        "verifier_entry": verifier,
        "reviewed_evidence_entry": evidence_entry,
        "semantic_expectations": semantic_expectations,
        "top_level_artifact_catalog": top_catalog,
        "top_level_artifact_role_count": len(top_catalog),
        "rehearsal_artifact_catalog": rehearsal_catalog,
        "rehearsal_artifact_role_count": len(rehearsal_catalog),
        "application_roots": application_roots,
        "application_stage_roots": stage_roots,
        "broker_public_keys": public_keys,
        "network_authorized": False,
        "executable": False,
        "authorization_eligible": False,
        "candidate_self_check_is_independent_review": False,
        "contains_credentials": False,
        "contains_market_values": False,
        "contains_absolute_paths": False,
        "prospective_holdout_accessed": False,
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "gpu_job_count": 0,
        "max_read_date": "20260630",
    }
    payload = semantic | {"content_hash": _hash(semantic)}
    target = Path(output_path) if output_path else repository / ANCHOR_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if target.exists() and target.read_bytes() != encoded:
        raise Task055KR2ReleaseError("task055kr2_anchor_replacement_forbidden")
    target.write_bytes(encoded)
    return payload | {
        "manifest_path": str(target),
        "external_digest": hashlib.sha256(encoded).hexdigest(),
    }


def publish_supersession(*, repository_root: str | Path) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    path = repository / SUPERSESSION_PATH
    if not path.is_file():
        raise Task055KR2ReleaseError("task055kr2_supersession_missing")
    payload = _json_object(path.read_bytes(), code="supersession")
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("status") != "superseded" or payload.get("content_hash") != _hash(semantic):
        raise Task055KR2ReleaseError("task055kr2_supersession_invalid")
    return payload | {"manifest_path": str(path)}


def _validate_candidate_evidence(payload: Mapping[str, Any]) -> None:
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    required_false = (
        "network_authorized",
        "executable",
        "authorization_eligible",
        "contains_credentials",
        "contains_market_values",
        "contains_absolute_paths",
        "prospective_holdout_accessed",
        "production_execution_ancestor",
        "certification_ready",
        "portfolio_ready",
        "optimizer_ready",
        "paper_ready",
        "live_ready",
    )
    if (
        payload.get("status") != CANDIDATE_STATUS
        or payload.get("baseline_commit") != BASELINE_COMMIT
        or payload.get("content_hash") != _hash(semantic)
        or any(payload.get(key) is not False for key in required_false)
        or payload.get("operational_state_unproven") is not True
    ):
        raise Task055KR2ReleaseError("task055kr2_candidate_evidence_invalid")


def _validate_artifact_catalog(
    value: Any, *, governed: Path
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise Task055KR2ReleaseError("task055kr2_artifact_catalog_missing")
    rows = [dict(row) for row in value]
    roles = [str(row.get("role") or "") for row in rows]
    if not all(roles) or len(roles) != len(set(roles)):
        raise Task055KR2ReleaseError("task055kr2_artifact_role_cardinality_invalid")
    for row in rows:
        relative = Path(str(row.get("relative_path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055KR2ReleaseError("task055kr2_artifact_path_invalid")
        path = governed / relative
        if not path.is_file() or path.is_symlink():
            raise Task055KR2ReleaseError(
                f"task055kr2_artifact_missing_or_unsafe:{row['role']}"
            )
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row.get("sha256"):
            raise Task055KR2ReleaseError(
                f"task055kr2_artifact_sha_invalid:{row['role']}"
            )
        payload = _json_object(raw, code=f"artifact:{row['role']}")
        semantic = {
            key: value
            for key, value in payload.items()
            if key not in {"content_hash", "generation_id"}
        }
        if payload.get("content_hash") != _hash(semantic):
            raise Task055KR2ReleaseError(
                f"task055kr2_artifact_content_hash_invalid:{row['role']}"
            )
        if payload.get("content_hash") != row.get("content_hash"):
            raise Task055KR2ReleaseError(
                f"task055kr2_artifact_catalog_content_invalid:{row['role']}"
            )
    return sorted(rows, key=lambda row: str(row["role"]))


def _application_roots(
    *, governed: Path, rehearsal_catalog: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    selected = {
        row["role"]: row
        for row in rehearsal_catalog
        if str(row["role"]).endswith("_application")
    }
    required = {
        "positive_primary_application",
        "positive_sibling_application",
        "empty_primary_application",
        "empty_sibling_application",
    }
    if set(selected) != required:
        raise Task055KR2ReleaseError("task055kr2_application_artifact_set_invalid")
    stage_roots: dict[str, list[dict[str, Any]]] = {}
    application_roots: dict[str, str] = {}
    for role in sorted(required):
        row = selected[role]
        payload = _json_object(
            (governed / str(row["relative_path"])).read_bytes(), code=role
        )
        stages = [dict(stage) for stage in payload.get("stages") or ()]
        if len(stages) != 12 or [stage.get("ordinal") for stage in stages] != list(
            range(1, 13)
        ):
            raise Task055KR2ReleaseError(f"task055kr2_application_stage_set_invalid:{role}")
        stage_roots[role] = stages
        application_roots[role] = str(payload["content_hash"])
    return stage_roots, application_roots


def _load_rehearsal_release_catalog(
    *,
    governed: Path,
    top_catalog: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    rehearsal_row = next(
        (row for row in top_catalog if row["role"] == "native_rehearsal"),
        None,
    )
    if rehearsal_row is None:
        raise Task055KR2ReleaseError("task055kr2_native_rehearsal_role_missing")
    rehearsal_path = governed / str(rehearsal_row["relative_path"])
    rehearsal = _json_object(rehearsal_path.read_bytes(), code="native_rehearsal")
    try:
        rehearsal_root = rehearsal_path.parents[3]
    except IndexError:
        raise Task055KR2ReleaseError("task055kr2_rehearsal_layout_invalid") from None
    rehearsal_catalog = _validate_artifact_catalog(
        rehearsal.get("artifact_catalog"),
        governed=rehearsal_root,
    )
    return rehearsal, rehearsal_catalog, rehearsal_root


def _reservation_public_keys(
    *, governed: Path, rehearsal_catalog: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for row in rehearsal_catalog:
        role = str(row["role"])
        if "reservation" not in role:
            continue
        payload = _json_object(
            (governed / str(row["relative_path"])).read_bytes(), code=role
        )
        encoded = str(payload.get("broker_public_key_pem_b64") or "")
        key_hash = str(payload.get("broker_public_key_sha256") or "")
        if not encoded or len(key_hash) != 64:
            raise Task055KR2ReleaseError(f"task055kr2_reservation_public_key_missing:{role}")
        results[role] = {
            "broker_public_key_pem_b64": encoded,
            "broker_public_key_sha256": key_hash,
            "reservation_content_hash": str(payload["content_hash"]),
        }
    if len(results) != 2:
        raise Task055KR2ReleaseError("task055kr2_reservation_public_key_set_invalid")
    return results


def _release_source_entries(repository: Path, treeish: str) -> list[dict[str, Any]]:
    raw = subprocess.run(
        ["git", "ls-tree", "-r", "-z", treeish],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    rows = []
    for record in (item for item in raw.split("\0") if item):
        metadata, relative = record.split("\t", 1)
        mode, kind, blob = metadata.split()
        if kind != "blob" or not _release_source_path(relative):
            continue
        content = _git_blob(repository, blob)
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


def _release_source_path(relative: str) -> bool:
    path = Path(relative)
    if relative in {"pyproject.toml", "uv.lock", "AGENTS.md"}:
        return True
    if path.suffix != ".py":
        return False
    if relative.startswith(("tests/", "evidence/")):
        return False
    return True


def _git_blob_entry(repository: Path, treeish: str, relative: str) -> dict[str, Any]:
    raw = subprocess.run(
        ["git", "ls-tree", treeish, "--", relative],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if not raw:
        raise Task055KR2ReleaseError(f"task055kr2_git_blob_missing:{relative}")
    metadata, path = raw.split("\t", 1)
    mode, kind, blob = metadata.split()
    if path != relative or kind != "blob" or mode not in {"100644", "100755"}:
        raise Task055KR2ReleaseError(f"task055kr2_git_blob_invalid:{relative}")
    content = _git_blob(repository, blob)
    return {
        "path": relative,
        "git_blob_id": blob,
        "git_index_mode": mode,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _git_blob(repository: Path, blob: str) -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", blob],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def _single_parent(repository: Path, commit: str) -> str:
    parts = _git(repository, "rev-list", "--parents", "-n", "1", commit).split()
    if len(parts) != 2:
        raise Task055KR2ReleaseError("task055kr2_release_commit_must_have_single_parent")
    return parts[1]


def _changed_paths(repository: Path, start: str, end: str) -> list[str]:
    return sorted(
        line
        for line in _git(repository, "diff", "--name-only", f"{start}..{end}").splitlines()
        if line
    )


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repository,
        check=False,
    ).returncode == 0


def _json_object(payload: bytes, *, code: str) -> dict[str, Any]:
    try:
        row = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Task055KR2ReleaseError(f"task055kr2_json_invalid:{code}") from None
    if not isinstance(row, dict):
        raise Task055KR2ReleaseError(f"task055kr2_json_object_required:{code}")
    return row


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task055-KR2 candidate anchor publisher")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--governed-root", required=True)
    args = parser.parse_args(argv)
    result = build_candidate_anchor(
        repository_root=args.repository_root,
        implementation_commit=args.implementation_commit,
        evidence_commit=args.evidence_commit,
        governed_root=args.governed_root,
    )
    supersession = publish_supersession(repository_root=args.repository_root)
    print(
        json.dumps(
            {
                "status": result["status"],
                "anchor_content_hash": result["content_hash"],
                "external_digest": result["external_digest"],
                "supersession_content_hash": supersession["content_hash"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


BASELINE_COMMIT = "df24308eadab07128b9efead884355247e58a382"
ANCHOR_SCHEMA = "task055kr2_external_release_candidate_anchor_v1"
ANCHOR_PATH = "evidence/task_055_k/task055kr2_candidate_anchor.json"
CANDIDATE_EVIDENCE_PATH = "evidence/task_055_k/task055kr2_candidate_evidence.json"
LEGACY_EVIDENCE_PATH = "evidence/task_055_k/task055kr_scrubbed_evidence.json"
CANDIDATE_STATUS = "task055kr2_candidate_ready_for_independent_audit_no_network_executed"
CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vol",
        "amount",
    ],
    "request_fingerprint": "8cec7ae0957a9d54afb1f08736db3f1c12b402554f5e1c3cc2e007658b8af869",
    "transport_identity": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_identity": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}


class Task055KVerifierError(RuntimeError):
    pass


def verify_release_candidate(
    *,
    repository_root: str | Path,
    anchor_commit: str,
    anchor_digest: str,
    governed_root: str | Path,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    if not _hex(anchor_commit, 40) or not _hex(anchor_digest, 64):
        raise Task055KVerifierError("task055kr2_exact_anchor_and_digest_required")
    anchor_bytes = _git_path_bytes(repository, anchor_commit, ANCHOR_PATH)
    if hashlib.sha256(anchor_bytes).hexdigest() != anchor_digest:
        raise Task055KVerifierError("task055kr2_external_anchor_digest_invalid")
    anchor = _json_object(anchor_bytes, code="anchor")
    _verify_anchor_self(anchor)
    _verify_release_git_topology(
        repository=repository,
        anchor_commit=anchor_commit,
        anchor=anchor,
        anchor_bytes=anchor_bytes,
    )
    _verify_source_tree(repository=repository, anchor=anchor)
    evidence_bytes = _git_path_bytes(repository, anchor_commit, CANDIDATE_EVIDENCE_PATH)
    evidence = _json_object(evidence_bytes, code="candidate_evidence")
    verify_candidate_semantics(anchor=anchor, candidate=evidence)
    _verify_artifact_closure(anchor=anchor, governed=governed)
    _verify_receipt_signatures(anchor=anchor, governed=governed)
    _verify_no_sensitive_content(anchor_bytes, evidence_bytes)
    return {
        "status": "passed",
        "top_status": CANDIDATE_STATUS,
        "anchor_commit": anchor_commit,
        "anchor_digest": anchor_digest,
        "implementation_commit": anchor["release_topology"]["implementation_commit"],
        "evidence_commit": anchor["release_topology"]["evidence_commit"],
        "source_root": anchor["source_root"],
        "artifact_root": _hash(
            {
                "top": anchor["top_level_artifact_catalog"],
                "rehearsal": anchor["rehearsal_artifact_catalog"],
            }
        ),
        "candidate_self_check_is_independent_review": False,
        "network_authorized": False,
        "authorization_eligible": False,
    }


def verify_candidate_semantics(
    *, anchor: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    _verify_anchor_self(anchor)
    semantic = {key: value for key, value in candidate.items() if key != "content_hash"}
    if candidate.get("content_hash") != _hash(semantic):
        raise Task055KVerifierError("task055kr2_candidate_self_hash_invalid")
    expected = anchor.get("semantic_expectations") or {}
    ordered_checks = (
        "status",
        "implementation_commit",
        "baseline_commit",
        "ordered_exact_daily_keys",
        "ordered_key_root",
        "canary",
        "budgets",
        "root_bindings",
        "artifact_catalog",
        "artifact_catalog_root",
        "lineage",
        "cross_lineage",
        "application_stage_order",
        "application_role_roots",
        "synthetic_receipt_attestations",
        "broker_contract_hash",
        "network_authorized",
        "executable",
        "authorization_eligible",
        "operational_state_unproven",
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
    if set(expected) != set(ordered_checks):
        raise Task055KVerifierError("task055kr2_anchor_semantic_expectation_set_invalid")
    for key in ordered_checks:
        if candidate.get(key) != expected[key]:
            raise Task055KVerifierError(f"task055kr2_semantic_mismatch:{key}")
    keys = candidate["ordered_exact_daily_keys"]
    if len(keys) != 17 or keys[0] != {"ordinal": 1, **CANARY}:
        raise Task055KVerifierError("task055kr2_exact17_or_first_canary_invalid")
    if _hash(keys) != candidate["ordered_key_root"]:
        raise Task055KVerifierError("task055kr2_ordered_key_root_invalid")
    catalog = candidate["artifact_catalog"]
    roles = [row.get("role") for row in catalog]
    if len(roles) != len(set(roles)) or catalog != sorted(
        catalog, key=lambda row: str(row["role"])
    ):
        raise Task055KVerifierError("task055kr2_candidate_artifact_roles_invalid")
    if _hash(catalog) != candidate["artifact_catalog_root"]:
        raise Task055KVerifierError("task055kr2_candidate_artifact_root_invalid")
    return {
        "status": "passed",
        "candidate_content_hash": candidate["content_hash"],
    }


def verify_scrubbed_evidence(
    evidence_path: str | Path,
    *,
    repository_root: str | Path,
    require_git_attestation: bool = True,
    anchor_commit: str | None = None,
    anchor_digest: str | None = None,
    governed_root: str | Path | None = None,
) -> dict[str, Any]:
    del evidence_path, require_git_attestation
    if anchor_commit is None or anchor_digest is None or governed_root is None:
        raise Task055KVerifierError("task055kr2_external_release_anchor_required")
    return verify_release_candidate(
        repository_root=repository_root,
        anchor_commit=anchor_commit,
        anchor_digest=anchor_digest,
        governed_root=governed_root,
    )


def verify_mutated_payload_against_trusted_evidence(*_args, **_kwargs) -> dict[str, Any]:
    raise Task055KVerifierError("task055kr2_external_release_anchor_required")


def _verify_anchor_self(anchor: Mapping[str, Any]) -> None:
    semantic = {key: value for key, value in anchor.items() if key != "content_hash"}
    if (
        anchor.get("schema_version") != ANCHOR_SCHEMA
        or anchor.get("status") != CANDIDATE_STATUS
        or anchor.get("content_hash") != _hash(semantic)
        or anchor.get("candidate_self_check_is_independent_review") is not False
        or anchor.get("network_authorized") is not False
        or anchor.get("executable") is not False
        or anchor.get("authorization_eligible") is not False
    ):
        raise Task055KVerifierError("task055kr2_anchor_contract_invalid")


def _verify_release_git_topology(
    *,
    repository: Path,
    anchor_commit: str,
    anchor: Mapping[str, Any],
    anchor_bytes: bytes,
) -> None:
    if _git(repository, "status", "--porcelain"):
        raise Task055KVerifierError("task055kr2_target_worktree_dirty")
    if _git(repository, "rev-parse", "HEAD") != anchor_commit:
        raise Task055KVerifierError("task055kr2_target_head_not_exact_anchor")
    topology = anchor.get("release_topology") or {}
    implementation = str(topology.get("implementation_commit") or "")
    evidence = str(topology.get("evidence_commit") or "")
    if (
        topology.get("baseline_commit") != BASELINE_COMMIT
        or not _hex(implementation, 40)
        or not _hex(evidence, 40)
        or not _is_ancestor(repository, BASELINE_COMMIT, implementation)
        or _single_parent(repository, evidence) != implementation
        or _single_parent(repository, anchor_commit) != evidence
    ):
        raise Task055KVerifierError("task055kr2_release_commit_topology_invalid")
    expected_evidence = sorted(topology.get("evidence_commit_allowlist") or ())
    expected_anchor = sorted(topology.get("anchor_commit_allowlist") or ())
    if (
        _changed_paths(repository, implementation, evidence) != expected_evidence
        or _changed_paths(repository, evidence, anchor_commit) != expected_anchor
    ):
        raise Task055KVerifierError("task055kr2_release_path_allowlist_invalid")
    working_anchor = (repository / ANCHOR_PATH).read_bytes()
    if working_anchor != anchor_bytes:
        raise Task055KVerifierError("task055kr2_anchor_worktree_blob_invalid")
    evidence_entry = anchor.get("reviewed_evidence_entry") or {}
    if _git_blob_entry(repository, evidence, CANDIDATE_EVIDENCE_PATH) != evidence_entry:
        raise Task055KVerifierError("task055kr2_reviewed_evidence_blob_invalid")
    if _git_blob_entry(repository, anchor_commit, CANDIDATE_EVIDENCE_PATH) != evidence_entry:
        raise Task055KVerifierError("task055kr2_evidence_changed_after_e_commit")
    legacy = anchor.get("legacy_evidence_entry") or {}
    if (
        _git_blob_entry(repository, BASELINE_COMMIT, LEGACY_EVIDENCE_PATH) != legacy
        or _git_blob_entry(repository, anchor_commit, LEGACY_EVIDENCE_PATH) != legacy
    ):
        raise Task055KVerifierError("task055kr2_legacy_evidence_rewritten")


def _verify_source_tree(*, repository: Path, anchor: Mapping[str, Any]) -> None:
    implementation = anchor["release_topology"]["implementation_commit"]
    expected = _release_source_entries(repository, implementation)
    if expected != anchor.get("source_entries") or _hash(expected) != anchor.get(
        "source_root"
    ):
        raise Task055KVerifierError("task055kr2_runtime_source_tree_invalid")
    verifier = next(
        (row for row in expected if row["path"] == "task_055_k/verifier.py"), None
    )
    if verifier != anchor.get("verifier_entry"):
        raise Task055KVerifierError("task055kr2_verifier_blob_anchor_invalid")
    executing = Path(__file__).read_bytes()
    if hashlib.sha256(executing).hexdigest() != verifier["sha256"]:
        raise Task055KVerifierError("task055kr2_executing_verifier_not_implementation_blob")


def _verify_artifact_closure(*, anchor: Mapping[str, Any], governed: Path) -> None:
    for catalog_name, count_name in (
        ("top_level_artifact_catalog", "top_level_artifact_role_count"),
        ("rehearsal_artifact_catalog", "rehearsal_artifact_role_count"),
    ):
        expected = anchor.get(catalog_name) or []
        if not isinstance(expected, list) or len(expected) != anchor.get(count_name):
            raise Task055KVerifierError(f"task055kr2_{catalog_name}_count_invalid")
        roles = [row.get("role") for row in expected]
        if len(roles) != len(set(roles)):
            raise Task055KVerifierError(f"task055kr2_{catalog_name}_roles_invalid")
        for row in expected:
            _verify_artifact_row(governed=governed, row=row)
    rehearsal_row = next(
        row
        for row in anchor["top_level_artifact_catalog"]
        if row["role"] == "native_rehearsal"
    )
    rehearsal = _json_object(
        (governed / rehearsal_row["relative_path"]).read_bytes(),
        code="native_rehearsal",
    )
    actual_rehearsal_catalog = sorted(
        [dict(row) for row in rehearsal.get("artifact_catalog") or ()],
        key=lambda row: str(row["role"]),
    )
    if actual_rehearsal_catalog != anchor["rehearsal_artifact_catalog"]:
        raise Task055KVerifierError("task055kr2_rehearsal_catalog_invalid")
    expected_apps = anchor.get("application_roots") or {}
    expected_stages = anchor.get("application_stage_roots") or {}
    actual_apps: dict[str, str] = {}
    actual_stages: dict[str, list[dict[str, Any]]] = {}
    for row in actual_rehearsal_catalog:
        role = str(row["role"])
        if not role.endswith("_application"):
            continue
        payload = _json_object(
            (governed / row["relative_path"]).read_bytes(), code=role
        )
        actual_apps[role] = str(payload.get("content_hash") or "")
        actual_stages[role] = [dict(stage) for stage in payload.get("stages") or ()]
    if actual_apps != expected_apps or actual_stages != expected_stages:
        raise Task055KVerifierError("task055kr2_application_stage_roots_invalid")
    stage_order = anchor["semantic_expectations"]["application_stage_order"]
    for role, stages in actual_stages.items():
        if [row.get("stage") for row in stages] != stage_order or [
            row.get("ordinal") for row in stages
        ] != list(range(1, 13)):
            raise Task055KVerifierError(f"task055kr2_application_stage_order_invalid:{role}")


def _verify_artifact_row(*, governed: Path, row: Mapping[str, Any]) -> None:
    relative = Path(str(row.get("relative_path") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055KVerifierError("task055kr2_artifact_relative_path_invalid")
    path = governed / relative
    if not path.is_file() or path.is_symlink():
        raise Task055KVerifierError(f"task055kr2_artifact_missing:{row.get('role')}")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row.get("sha256"):
        raise Task055KVerifierError(f"task055kr2_artifact_sha_invalid:{row.get('role')}")
    payload = _json_object(raw, code=f"artifact:{row.get('role')}")
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"content_hash", "generation_id"}
    }
    if payload.get("content_hash") != _hash(semantic) or payload.get(
        "content_hash"
    ) != row.get("content_hash"):
        raise Task055KVerifierError(
            f"task055kr2_artifact_content_hash_invalid:{row.get('role')}"
        )


def _verify_receipt_signatures(*, anchor: Mapping[str, Any], governed: Path) -> None:
    catalog = {row["role"]: row for row in anchor["rehearsal_artifact_catalog"]}
    keys = anchor.get("broker_public_keys") or {}
    if len(keys) != 2:
        raise Task055KVerifierError("task055kr2_broker_public_key_set_invalid")
    for reservation_role, key in keys.items():
        reservation_row = catalog.get(reservation_role)
        if reservation_row is None:
            raise Task055KVerifierError("task055kr2_reservation_artifact_missing")
        reservation = _json_object(
            (governed / reservation_row["relative_path"]).read_bytes(),
            code=reservation_role,
        )
        if (
            reservation.get("content_hash") != key.get("reservation_content_hash")
            or reservation.get("broker_public_key_pem_b64")
            != key.get("broker_public_key_pem_b64")
            or reservation.get("broker_public_key_sha256")
            != key.get("broker_public_key_sha256")
        ):
            raise Task055KVerifierError("task055kr2_reservation_public_key_invalid")
        branch = "positive" if reservation_role.startswith("positive") else "empty"
        receipt_roles = [
            role for role in catalog
            if role.startswith(branch) and "receipt" in role
        ]
        if len(receipt_roles) != 1:
            raise Task055KVerifierError("task055kr2_receipt_role_cardinality_invalid")
        receipt = _json_object(
            (governed / catalog[receipt_roles[0]]["relative_path"]).read_bytes(),
            code=receipt_roles[0],
        )
        if receipt.get("attempt_reservation_content_hash") != reservation.get(
            "content_hash"
        ):
            raise Task055KVerifierError("task055kr2_receipt_reservation_lineage_invalid")
        for identity in (
            "request_fingerprint",
            "transport_identity",
            "evidence_use_identity",
        ):
            if receipt.get(identity) != CANARY[identity]:
                raise Task055KVerifierError(f"task055kr2_receipt_identity_invalid:{identity}")
        signed = {
            field: value
            for field, value in receipt.items()
            if field not in {"signature", "content_hash", "generation_id"}
        }
        public_key = base64.b64decode(
            str(key["broker_public_key_pem_b64"]), validate=True
        )
        if _hash(public_key.decode("ascii")) != key["broker_public_key_sha256"]:
            raise Task055KVerifierError("task055kr2_anchor_public_key_hash_invalid")
        _openssl_verify(
            public_key=public_key,
            payload=_canonical_bytes(signed),
            signature_b64=str(receipt.get("signature") or ""),
        )


def _openssl_verify(*, public_key: bytes, payload: bytes, signature_b64: str) -> None:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except ValueError:
        raise Task055KVerifierError("task055kr2_receipt_signature_encoding_invalid") from None
    with tempfile.TemporaryDirectory(prefix="task055kr2-verify-") as directory:
        key_path = Path(directory) / "key.pem"
        signature_path = Path(directory) / "signature.bin"
        key_path.write_bytes(public_key)
        signature_path.write_bytes(signature)
        result = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-verify",
                str(key_path),
                "-signature",
                str(signature_path),
            ],
            input=payload,
            capture_output=True,
            check=False,
        )
    if result.returncode:
        raise Task055KVerifierError("task055kr2_receipt_signature_invalid")


def _verify_no_sensitive_content(*payloads: bytes) -> None:
    forbidden = (b"/home/", b"TUSHARE_TOKEN", b"credential_file", b"token_hash")
    if any(marker in payload for payload in payloads for marker in forbidden):
        raise Task055KVerifierError("task055kr2_sensitive_content_detected")


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
    return path.suffix == ".py" and not relative.startswith(("tests/", "evidence/"))


def _git_blob_entry(repository: Path, treeish: str, relative: str) -> dict[str, Any]:
    line = _git(repository, "ls-tree", treeish, "--", relative)
    if not line:
        raise Task055KVerifierError(f"task055kr2_git_blob_missing:{relative}")
    metadata, path = line.split("\t", 1)
    mode, kind, blob = metadata.split()
    if path != relative or kind != "blob" or mode not in {"100644", "100755"}:
        raise Task055KVerifierError(f"task055kr2_git_blob_invalid:{relative}")
    content = _git_blob(repository, blob)
    return {
        "path": relative,
        "git_blob_id": blob,
        "git_index_mode": mode,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _git_path_bytes(repository: Path, treeish: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{treeish}:{relative}"],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise Task055KVerifierError(f"task055kr2_git_path_missing:{treeish}:{relative}")
    return result.stdout


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
        raise Task055KVerifierError("task055kr2_release_commit_not_single_parent")
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
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Task055KVerifierError(f"task055kr2_json_invalid:{code}") from None
    if not isinstance(value, dict):
        raise Task055KVerifierError(f"task055kr2_json_object_required:{code}")
    return value


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise Task055KVerifierError(f"task055kr2_git_command_failed:{' '.join(args)}")
    return result.stdout.strip()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _hex(value: Any, length: int) -> bool:
    text = str(value or "")
    return len(text) == length and all(character in "0123456789abcdef" for character in text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task055-KR2 external-anchor verifier")
    subparsers = parser.add_subparsers(dest="command", required=True)
    authoritative = subparsers.add_parser("authoritative")
    authoritative.add_argument("--repository-root", required=True)
    authoritative.add_argument("--anchor-commit", required=True)
    authoritative.add_argument("--anchor-digest", required=True)
    authoritative.add_argument("--governed-root", required=True)
    semantic = subparsers.add_parser("semantic")
    semantic.add_argument("--anchor-file", required=True)
    semantic.add_argument("--anchor-digest", required=True)
    semantic.add_argument("--candidate-evidence", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "authoritative":
            result = verify_release_candidate(
                repository_root=args.repository_root,
                anchor_commit=args.anchor_commit,
                anchor_digest=args.anchor_digest,
                governed_root=args.governed_root,
            )
        else:
            anchor_bytes = Path(args.anchor_file).read_bytes()
            if hashlib.sha256(anchor_bytes).hexdigest() != args.anchor_digest:
                raise Task055KVerifierError("task055kr2_external_anchor_digest_invalid")
            result = verify_candidate_semantics(
                anchor=_json_object(anchor_bytes, code="external_anchor"),
                candidate=_json_object(
                    Path(args.candidate_evidence).read_bytes(), code="candidate_evidence"
                ),
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

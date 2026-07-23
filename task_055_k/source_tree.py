from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from task_055_h.io import canonical_hash, validate_generation

from .contracts import SOURCE_SCHEMA
from .immutable import write_immutable_generation


class Task055KSourceTreeError(RuntimeError):
    pass


def publish_git_index_source_seal(
    *, repository_root: str | Path, output_root: str | Path, implementation_commit: str
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    _require_clean(repository)
    if _git(repository, "rev-parse", "HEAD") != implementation_commit:
        raise Task055KSourceTreeError("task055k_implementation_commit_mismatch")
    entries = git_index_source_entries(repository)
    semantic = {
        "schema_version": SOURCE_SCHEMA,
        "status": "sealed",
        "implementation_commit": implementation_commit,
        "identity_basis": "git_index_blob_mode_sha256_size",
        "entries": entries,
        "entry_count": len(entries),
        "source_root": canonical_hash(entries),
        "server_permission_audit": _server_permission_audit(repository, entries),
    }
    return write_immutable_generation(
        output_root,
        prefix="task055k_git_index_source",
        manifest_name="source_seal.json",
        semantic=semantic,
    )


def validate_git_index_source_seal(
    path: str | Path,
    *,
    repository_root: str | Path,
    require_clean: bool,
    allowed_evidence_descendant: bool,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    payload = validate_generation(path, schema=SOURCE_SCHEMA, manifest_name="source_seal.json")
    if require_clean:
        _require_clean(repository)
    implementation = str(payload.get("implementation_commit") or "")
    head = _git(repository, "rev-parse", "HEAD")
    if head != implementation:
        if not allowed_evidence_descendant or subprocess.run(
            ["git", "merge-base", "--is-ancestor", implementation, head],
            cwd=repository,
            check=False,
        ).returncode:
            raise Task055KSourceTreeError("task055k_implementation_commit_not_current_ancestor")
        changed = _git(repository, "diff", "--name-only", f"{implementation}..{head}").splitlines()
        if any(not _evidence_only(name) for name in changed):
            raise Task055KSourceTreeError("task055k_post_implementation_runtime_drift")
    entries = git_index_source_entries(repository, treeish=implementation)
    if entries != payload.get("entries") or canonical_hash(entries) != payload.get("source_root"):
        raise Task055KSourceTreeError("task055k_git_index_source_root_mismatch")
    audit = _server_permission_audit(repository, entries)
    if audit != payload.get("server_permission_audit") or audit.get("unsafe_count") != 0:
        raise Task055KSourceTreeError("task055k_server_runtime_permission_unsafe")
    return payload | {"repository_root": str(repository), "current_head": head}


def git_index_source_entries(repository: Path, *, treeish: str | None = None) -> list[dict[str, Any]]:
    if treeish is None:
        raw = _git(repository, "ls-files", "-s", "-z")
        records = [value for value in raw.split("\0") if value]
        parsed = []
        for record in records:
            metadata, relative = record.split("\t", 1)
            mode, blob, _stage = metadata.split()
            parsed.append((mode, blob, relative))
    else:
        raw = subprocess.run(
            ["git", "ls-tree", "-r", "-z", treeish],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout.decode()
        parsed = []
        for record in (value for value in raw.split("\0") if value):
            metadata, relative = record.split("\t", 1)
            mode, kind, blob = metadata.split()
            if kind == "blob":
                parsed.append((mode, blob, relative))
    entries: list[dict[str, Any]] = []
    for mode, blob, relative in sorted(parsed, key=lambda value: value[2]):
        if not _included(relative):
            continue
        if mode not in {"100644", "100755"}:
            raise Task055KSourceTreeError(f"task055k_git_index_mode_invalid:{relative}:{mode}")
        content = subprocess.run(
            ["git", "cat-file", "blob", blob],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout
        entries.append(
            {
                "path": relative,
                "git_blob_id": blob,
                "git_index_mode": mode,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    required = {
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
        "dev_tools/task055kr_harness.py",
        "dev_tools/task055kr_mutations.py",
        "data_pipeline/ashare/network_capability.py",
        "data_pipeline/ashare/providers/tushare_client.py",
    }
    if not required.issubset({entry["path"] for entry in entries}):
        raise Task055KSourceTreeError("task055k_runtime_source_selection_incomplete")
    return entries


def _server_permission_audit(repository: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    unsafe = []
    for row in entries:
        path = repository / row["path"]
        if not path.is_file() or path.is_symlink():
            raise Task055KSourceTreeError(f"task055k_runtime_source_file_invalid:{row['path']}")
        mode = path.stat().st_mode & 0o777
        if mode & 0o022:
            unsafe.append({"path": row["path"], "server_mode": format(mode, "04o")})
    return {"policy": "reject_group_or_world_writable_runtime_files", "unsafe_count": len(unsafe), "unsafe": unsafe}


def _included(relative: str) -> bool:
    path = Path(relative)
    if relative.startswith(("tests/", "evidence/", "assets/", "paper/", "lord/")):
        return False
    if path.suffix == ".py":
        return True
    if relative in {"requirements.txt", "requirements-optional.txt", "environment.yml", ".env.example"}:
        return True
    return relative in {"pyproject.toml", "uv.lock"} or relative.startswith(".github/workflows/")


def _evidence_only(relative: str) -> bool:
    return relative in {"README.md", "CATREADME.md", "FRAMEWORK_UPDATE.md"} or relative.startswith("evidence/task_055_k/")


def _require_clean(repository: Path) -> None:
    if _git(repository, "status", "--porcelain"):
        raise Task055KSourceTreeError("task055k_clean_worktree_required")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, text=True, capture_output=True
    ).stdout.strip()

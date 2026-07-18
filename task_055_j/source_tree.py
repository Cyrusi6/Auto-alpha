from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from task_055_h.io import canonical_hash, publish_generation, sha256_file, validate_generation

from .contracts import EVIDENCE_ONLY_PATHS, RUNTIME_SOURCE_FILES, RUNTIME_SOURCE_SUFFIXES, SOURCE_TREE_SCHEMA


class Task055JSourceTreeError(RuntimeError):
    pass


def publish_source_tree_seal(
    *, repository_root: str | Path, output_root: str | Path, implementation_commit: str
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    _require_clean(repository)
    if _git(repository, "rev-parse", "HEAD") != implementation_commit:
        raise Task055JSourceTreeError("task055j_source_seal_commit_not_head")
    entries = _source_entries(repository)
    semantic = {
        "schema_version": SOURCE_TREE_SCHEMA,
        "status": "sealed",
        "implementation_commit": implementation_commit,
        "entry_count": len(entries),
        "entries": entries,
        "source_root": canonical_hash(entries),
        "selection_contract": {
            "tracked_files_only": True,
            "production_python_excludes_tests": True,
            "runtime_config_and_dependency_locks_included": True,
        },
    }
    return publish_generation(
        output_root,
        prefix="task055j_source_tree",
        manifest_name="source_tree_seal.json",
        semantic=semantic,
    )


def validate_source_tree_seal(
    path: str | Path,
    *,
    repository_root: str | Path,
    require_clean: bool,
    allow_evidence_only_descendant: bool,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    payload = validate_generation(path, schema=SOURCE_TREE_SCHEMA, manifest_name="source_tree_seal.json")
    if require_clean:
        _require_clean(repository)
    actual = _source_entries(repository)
    if actual != payload.get("entries") or canonical_hash(actual) != payload.get("source_root"):
        raise Task055JSourceTreeError("task055j_runtime_source_tree_drift")
    implementation = str(payload.get("implementation_commit") or "")
    head = _git(repository, "rev-parse", "HEAD")
    if head != implementation:
        if not allow_evidence_only_descendant:
            raise Task055JSourceTreeError("task055j_head_not_implementation_commit")
        if subprocess.run(
            ["git", "merge-base", "--is-ancestor", implementation, head],
            cwd=repository,
            check=False,
        ).returncode:
            raise Task055JSourceTreeError("task055j_implementation_not_head_ancestor")
        changed = _git(repository, "diff", "--name-only", f"{implementation}..{head}").splitlines()
        forbidden = [name for name in changed if not _evidence_only(name)]
        if forbidden:
            raise Task055JSourceTreeError(f"task055j_post_implementation_runtime_drift:{forbidden[:5]}")
    return payload | {"repository_root": str(repository), "current_head": head}


def _source_entries(repository: Path) -> list[dict[str, Any]]:
    names = _git(repository, "ls-files", "-z").split("\0")
    entries: list[dict[str, Any]] = []
    for relative in sorted(name for name in names if name):
        if not _included(relative):
            continue
        path = repository / relative
        if not path.is_file() or path.is_symlink():
            raise Task055JSourceTreeError(f"task055j_tracked_runtime_source_invalid:{relative}")
        entries.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "mode": path.stat().st_mode & 0o777,
            }
        )
    if not entries or not any(row["path"].startswith("task_055_j/") for row in entries):
        raise Task055JSourceTreeError("task055j_source_selection_incomplete")
    return entries


def _included(relative: str) -> bool:
    path = Path(relative)
    if relative.startswith(("tests/", "evidence/", "assets/", "paper/", "lord/")):
        return False
    if path.suffix == ".py":
        return True
    if relative in RUNTIME_SOURCE_FILES:
        return True
    return path.suffix in RUNTIME_SOURCE_SUFFIXES and (
        relative == "pyproject.toml" or relative == "uv.lock" or relative.startswith(".github/workflows/")
    )


def _evidence_only(relative: str) -> bool:
    return relative in EVIDENCE_ONLY_PATHS[:3] or relative.startswith(EVIDENCE_ONLY_PATHS[3])


def _require_clean(repository: Path) -> None:
    if _git(repository, "status", "--porcelain"):
        raise Task055JSourceTreeError("task055j_clean_worktree_required")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, text=True, capture_output=True
    ).stdout.strip()

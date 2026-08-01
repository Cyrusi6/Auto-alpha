"""Stable source and repository path resolution for semantic lineage."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Iterable


def module_file(module: str) -> Path:
    spec = importlib.util.find_spec(module)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"auto_alpha_module_unresolved:{module}")
    path = Path(spec.origin).resolve()
    if not path.is_file():
        raise RuntimeError(f"auto_alpha_module_source_missing:{module}:{path}")
    return path


def semantic_source_hash(modules: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for module in modules:
        path = module_file(module)
        digest.update(module.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def repository_root(start: str | Path | None = None) -> Path:
    path = Path(start).resolve() if start is not None else Path(__file__).resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src/auto_alpha").is_dir():
            return candidate
    raise RuntimeError(f"auto_alpha_repository_root_unresolved:{path}")

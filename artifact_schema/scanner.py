"""Scan local directories and artifact catalogs for schema-known files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .registry import infer_artifact_type


SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}
DEFAULT_SUFFIXES = {".json", ".jsonl"}


def scan_artifact_dirs(
    dirs: Iterable[str | Path],
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[Path]:
    found: list[Path] = []
    for directory in dirs:
        root = Path(directory)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.name.endswith(".schema.json"):
                continue
            if path.suffix not in DEFAULT_SUFFIXES:
                continue
            if include_patterns and not any(path.match(pattern) or path.name == pattern for pattern in include_patterns):
                continue
            if exclude_patterns and any(path.match(pattern) or path.name == pattern for pattern in exclude_patterns):
                continue
            if infer_artifact_type(path) is not None or include_patterns:
                found.append(path)
    return sorted(set(found))


def paths_from_artifact_catalog(path: str | Path) -> list[Path]:
    catalog_path = Path(path)
    if not catalog_path.exists():
        return []
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    root = catalog_path.parent
    paths: list[Path] = []
    for entry in payload.get("entries", []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        item = entry.get("path")
        if not item:
            continue
        candidate = Path(str(item))
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.exists() and candidate.is_file():
            paths.append(candidate)
    return paths

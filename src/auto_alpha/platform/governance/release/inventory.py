"""Release dependency, module and CLI inventories."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from auto_alpha.cli import COMMANDS

from .models import CliInventory, DependencyInventory, ModuleInventory


PLATFORM_MODULES = [
    "auto_alpha.data",
    "auto_alpha.research",
    "auto_alpha.validation",
    "auto_alpha.portfolio",
    "auto_alpha.execution",
    "auto_alpha.platform",
]


DEPENDENCY_FILES = [
    "pyproject.toml",
    "requirements.txt",
    "requirements-optional.txt",
    "environment.yml",
    "uv.lock",
]


def build_dependency_inventory(root_dir: str | Path = ".") -> DependencyInventory:
    root = Path(root_dir)
    files: list[dict] = []
    dependencies: dict[str, list[str]] = {"project": [], "optional": []}
    for name in DEPENDENCY_FILES:
        path = root / name
        files.append(
            {
                "path": name,
                "exists": path.exists(),
                "sha256": _sha256(path) if path.exists() else None,
                "size_bytes": path.stat().st_size if path.exists() else 0,
            }
        )
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = payload.get("project", {})
        dependencies["project"] = list(project.get("dependencies", []) or [])
        optional = project.get("optional-dependencies", {}) or {}
        dependencies["optional"] = [f"{group}:{dep}" for group, items in optional.items() for dep in items]
    return DependencyInventory(files=files, dependencies=dependencies)


def build_module_inventory(root_dir: str | Path = ".") -> ModuleInventory:
    root = Path(root_dir)
    modules = []
    for name in PLATFORM_MODULES:
        path = root / "src" / Path(*name.split("."))
        modules.append(
            {
                "module": name,
                "path": str(path.relative_to(root)),
                "is_package": (path / "__init__.py").exists(),
                "exists": path.exists(),
                "included": path.exists() and (path / "__init__.py").exists(),
            }
        )
    return ModuleInventory(modules=modules)


def build_cli_inventory(root_dir: str | Path = ".") -> CliInventory:
    del root_dir
    entries = [
        {
            "module": spec.module,
            "command": f"auto-alpha {domain} {command}",
            "path": "src/auto_alpha/cli.py",
            "has_main": True,
        }
        for (domain, command), spec in sorted(COMMANDS.items())
    ]
    return CliInventory(entries=entries)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

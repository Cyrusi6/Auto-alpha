"""Repository layout migration and regression checks.

Task-numbered packages were useful while the platform was being bootstrapped,
but they are not stable architectural boundaries.  This module keeps the
one-time import migration deterministic and provides a CI-friendly audit that
prevents those packages from returning at the repository root.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


LEGACY_PACKAGE_MAP = {
    "task_051_a": "point_in_time.historical_audit",
    "task_052_a": "backfill_repair.governed_replay",
    "task_053_a": "feature_factory.engineering_replay",
    "task_054_a": "research_firewall.truth_evidence",
    "task_054_b": "research_firewall.production_sentinel",
    "task_054_c": "research_firewall.engineering_closure",
    "task_055_a": "live_readiness.holdout_simulation",
    "task_055_b": "live_readiness.valuation_remediation",
    "task_055_c": "live_readiness.native_replay",
    "task_055_d": "live_readiness.secure_acquisition",
    "task_055_e": "live_readiness.source_salvage",
    "task_055_f": "live_readiness.evidence_hardening",
    "task_055_g": "live_readiness.production_hardening",
    "task_055_h": "live_readiness.network_authorization",
    "task_055_i": "live_readiness.canary_authority",
    "task_055_j": "live_readiness.production_authority",
    "task_055_k": "live_readiness.correctness_closure",
}


@dataclass(frozen=True)
class LayoutAudit:
    status: str
    top_level_package_count: int
    source_package_count: int
    legacy_directory_count: int
    legacy_import_count: int
    legacy_packaging_entry_count: int
    legacy_directories: tuple[str, ...]
    legacy_imports: tuple[str, ...]
    legacy_packaging_entries: tuple[str, ...]


def rewrite_legacy_imports(root_dir: str | Path) -> tuple[str, ...]:
    root = Path(root_dir).resolve()
    changed: list[str] = []
    for path in _python_files(root):
        original = path.read_text(encoding="utf-8")
        updated = original
        for legacy, canonical in LEGACY_PACKAGE_MAP.items():
            updated = re.sub(
                rf"(?<![A-Za-z0-9_/]){re.escape(legacy)}\.",
                f"{canonical}.",
                updated,
            )
            updated = re.sub(
                rf"(?P<quote>['\"]){re.escape(legacy)}/",
                lambda match: f"{match.group('quote')}{canonical.replace('.', '/')}/",
                updated,
            )
            updated = re.sub(
                rf"(?m)^(\s*from\s+){re.escape(legacy)}(?=\.|\s)",
                rf"\1{canonical}",
                updated,
            )
            updated = re.sub(
                rf"(?m)^(\s*import\s+){re.escape(legacy)}(?=\.|\s|,)",
                rf"\1{canonical}",
                updated,
            )
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    return tuple(sorted(changed))


def rewrite_source_paths(root_dir: str | Path) -> tuple[str, ...]:
    root = Path(root_dir).resolve()
    source_packages = tuple(
        sorted(path.name for path in (root / "src").iterdir() if (path / "__init__.py").is_file())
    )
    changed: list[str] = []
    for path in _python_files(root):
        original = path.read_text(encoding="utf-8")
        updated = original
        for package in source_packages:
            updated = re.sub(
                rf"(?P<quote>['\"])(?P<path>{re.escape(package)}/[^'\"]+\.py)(?P=quote)",
                lambda match: f"{match.group('quote')}src/{match.group('path')}{match.group('quote')}",
                updated,
            )
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    return tuple(sorted(changed))


def rewrite_test_source_paths(root_dir: str | Path) -> tuple[str, ...]:
    root = Path(root_dir).resolve()
    source_packages = tuple(
        sorted(path.name for path in (root / "src").iterdir() if (path / "__init__.py").is_file())
    )
    changed: list[str] = []
    for path in sorted((root / "tests").glob("test_*.py")):
        original = path.read_text(encoding="utf-8")
        updated = original
        for package in source_packages:
            updated = re.sub(
                rf"Path\((?P<quote>['\"]){re.escape(package)}(?P<suffix>/[^'\"]*)?(?P=quote)\)",
                lambda match: (
                    f"Path({match.group('quote')}src/{package}{match.group('suffix') or ''}{match.group('quote')})"
                ),
                updated,
            )
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    return tuple(changed)


def audit_repository_layout(root_dir: str | Path) -> LayoutAudit:
    root = Path(root_dir).resolve()
    package_roots = (root, root / "src")
    legacy_directories = tuple(
        sorted(
            str(path.relative_to(root))
            for package_root in package_roots
            if package_root.is_dir()
            for path in package_root.iterdir()
            if path.is_dir() and path.name in LEGACY_PACKAGE_MAP
        )
    )
    legacy_imports: list[str] = []
    for path in _python_files(root):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(
                re.match(rf"\s*(from|import)\s+{re.escape(legacy)}(?:\.|\s|,|$)", line)
                for legacy in LEGACY_PACKAGE_MAP
            ):
                legacy_imports.append(f"{path.relative_to(root)}:{line_number}")
    packaging = (root / "pyproject.toml").read_text(encoding="utf-8")
    legacy_packaging_entries = tuple(
        sorted(legacy for legacy in LEGACY_PACKAGE_MAP if f'"{legacy}"' in packaging)
    )
    top_level_package_count = sum(
        1 for path in root.iterdir() if path.is_dir() and (path / "__init__.py").is_file()
    )
    source_package_count = sum(
        1
        for path in (root / "src").iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    )
    passed = not legacy_directories and not legacy_imports and not legacy_packaging_entries
    return LayoutAudit(
        status="passed" if passed else "blocked",
        top_level_package_count=top_level_package_count,
        source_package_count=source_package_count,
        legacy_directory_count=len(legacy_directories),
        legacy_import_count=len(legacy_imports),
        legacy_packaging_entry_count=len(legacy_packaging_entries),
        legacy_directories=legacy_directories,
        legacy_imports=tuple(legacy_imports),
        legacy_packaging_entries=legacy_packaging_entries,
    )


def _python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("audit", "rewrite-imports", "rewrite-source-paths", "rewrite-test-paths"),
    )
    parser.add_argument("--root-dir", default=".")
    args = parser.parse_args(argv)
    if args.command == "rewrite-imports":
        changed = rewrite_legacy_imports(args.root_dir)
        print(json.dumps({"changed_count": len(changed), "changed": changed}, indent=2))
        return 0
    if args.command == "rewrite-source-paths":
        changed = rewrite_source_paths(args.root_dir)
        print(json.dumps({"changed_count": len(changed), "changed": changed}, indent=2))
        return 0
    if args.command == "rewrite-test-paths":
        changed = rewrite_test_source_paths(args.root_dir)
        print(json.dumps({"changed_count": len(changed), "changed": changed}, indent=2))
        return 0
    audit = audit_repository_layout(args.root_dir)
    print(json.dumps(asdict(audit), indent=2))
    return 0 if audit.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

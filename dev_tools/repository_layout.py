"""Read-only enforcement for the six-domain repository architecture."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


DOMAIN_SUBSYSTEMS = {
    "data": ("ingestion", "lake", "matrix", "pit", "quality"),
    "research": ("discovery", "factors", "features", "formulas", "neural"),
    "validation": ("certification", "firewall", "lab"),
    "portfolio": ("construction", "risk", "simulation"),
    "execution": ("broker", "operations", "settlement", "trading"),
    "platform": ("artifacts", "compute", "governance", "network_authority", "observability"),
}

NESTED_SUBSYSTEMS = {
    "data/ingestion": ("pipeline", "repair"),
    "data/lake": ("catalog", "operations", "store"),
    "platform/observability": ("dashboard", "monitoring"),
}

REMOVED_INTERNAL_PACKAGES = (
    "data/ingestion/backfill",
    "data/ingestion/index",
    "data/ingestion/landing",
    "data/ingestion/observer",
    "data/ingestion/post_download",
    "data/ingestion/repair/governed_replay",
    "platform/network_authority/_internal",
)


LEGACY_PACKAGE_NAMES = (
    "alpha_experiment_store",
    "alpha_factory",
    "approval",
    "artifact_schema",
    "backfill_observer",
    "backfill_repair",
    "backtest",
    "broker_adapter",
    "broker_connectivity",
    "broker_file_gateway",
    "broker_mapping_certification",
    "broker_readonly_mirror",
    "broker_statement",
    "broker_uat_lab",
    "capacity_model",
    "certification_campaign_store",
    "ci",
    "compute_cluster",
    "corporate_actions",
    "cross_source_checks",
    "dashboard",
    "data_backfill",
    "data_lake",
    "data_pipeline",
    "data_quality_lab",
    "data_source_validation",
    "evaluation",
    "execution",
    "execution_plan",
    "experiment_orchestrator",
    "factor_certification",
    "factor_engine",
    "factor_lifecycle",
    "factor_store",
    "feature_factory",
    "feature_promotion",
    "formula_batch_eval",
    "formula_corpus",
    "formula_search",
    "go_live_gate",
    "incident_response",
    "leakage_audit",
    "live_readiness",
    "matrix_refresh",
    "matrix_store",
    "model_core",
    "model_registry",
    "monitoring",
    "neural_search",
    "operations",
    "operator_handoff",
    "paper_account",
    "performance_benchmark",
    "point_in_time",
    "portfolio_campaign_store",
    "portfolio_certification",
    "portfolio_lab",
    "portfolio_optimizer",
    "portfolio_research",
    "post_download_orchestrator",
    "production_orchestrator",
    "production_replay",
    "program_trading_compliance",
    "raw_data_index",
    "raw_data_landing",
    "real_data_ops",
    "reconciliation_center",
    "release_manager",
    "research",
    "research_data_readiness",
    "research_firewall",
    "research_suite",
    "risk_controls",
    "risk_model",
    "settlement_engine",
    "shadow_lab",
    "shadow_trading",
    "strategy_manager",
    "universe",
    "validation_campaign_store",
    "validation_lab",
    "validation_red_team",
)


REMOVED_TASK_PACKAGES = tuple(
    f"task_{number}"
    for number in (
        "051_a",
        "052_a",
        "053_a",
        "054_a",
        "054_b",
        "054_c",
        "055_a",
        "055_b",
        "055_c",
        "055_d",
        "055_e",
        "055_f",
        "055_g",
        "055_h",
        "055_i",
        "055_j",
        "055_k",
    )
)


@dataclass(frozen=True)
class LayoutAudit:
    status: str
    top_level_package_count: int
    source_package_count: int
    domain_count: int
    subsystem_count: int
    legacy_directory_count: int
    legacy_import_count: int
    legacy_packaging_entry_count: int
    legacy_directories: tuple[str, ...]
    legacy_imports: tuple[str, ...]
    legacy_packaging_entries: tuple[str, ...]
    domain_issues: tuple[str, ...]


def audit_repository_layout(root_dir: str | Path) -> LayoutAudit:
    root = Path(root_dir).resolve()
    source = root / "src"
    auto_alpha = source / "auto_alpha"
    legacy_names = set(LEGACY_PACKAGE_NAMES) | set(REMOVED_TASK_PACKAGES)
    legacy_directories = tuple(
        sorted(
            str(path.relative_to(root))
            for package_root in (root, source)
            if package_root.is_dir()
            for path in package_root.iterdir()
            if path.is_dir() and path.name in legacy_names
        )
    )
    legacy_imports = _legacy_imports(root, legacy_names)
    packaging = (root / "pyproject.toml").read_text(encoding="utf-8")
    legacy_packaging_entries = tuple(
        sorted(name for name in legacy_names if f'"{name}"' in packaging)
    )
    actual_domains = _child_packages(auto_alpha)
    issues: list[str] = []
    if actual_domains != tuple(sorted(DOMAIN_SUBSYSTEMS)):
        issues.append(f"domains:{actual_domains!r}")
    subsystem_count = 0
    for domain, expected in DOMAIN_SUBSYSTEMS.items():
        actual = _child_packages(auto_alpha / domain)
        if actual != tuple(sorted(expected)):
            issues.append(f"subsystems:{domain}:{actual!r}")
        subsystem_count += len(actual)
    for relative, expected in NESTED_SUBSYSTEMS.items():
        actual = _child_packages(auto_alpha / relative)
        if actual != tuple(sorted(expected)):
            issues.append(f"nested_subsystems:{relative}:{actual!r}")
    test_domains = tuple(
        sorted(
            path.name
            for path in (root / "tests").iterdir()
            if path.is_dir() and path.name != "__pycache__"
        )
    )
    if test_domains != tuple(sorted(DOMAIN_SUBSYSTEMS)):
        issues.append(f"test_domains:{test_domains!r}")
    root_tests = tuple(path.name for path in (root / "tests").glob("test_*.py"))
    if root_tests:
        issues.append(f"root_tests:{root_tests!r}")
    for relative in REMOVED_INTERNAL_PACKAGES:
        if any((auto_alpha / relative).rglob("*.py")):
            issues.append(f"removed_internal_package_present:{relative}")
    top_level_package_count = sum(
        1
        for path in root.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    )
    source_package_count = sum(
        1
        for path in source.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    )
    passed = not (
        legacy_directories
        or legacy_imports
        or legacy_packaging_entries
        or issues
        or source_package_count != 1
    )
    return LayoutAudit(
        status="passed" if passed else "blocked",
        top_level_package_count=top_level_package_count,
        source_package_count=source_package_count,
        domain_count=len(actual_domains),
        subsystem_count=subsystem_count,
        legacy_directory_count=len(legacy_directories),
        legacy_import_count=len(legacy_imports),
        legacy_packaging_entry_count=len(legacy_packaging_entries),
        legacy_directories=legacy_directories,
        legacy_imports=legacy_imports,
        legacy_packaging_entries=legacy_packaging_entries,
        domain_issues=tuple(issues),
    )


def _child_packages(root: Path) -> tuple[str, ...]:
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir() and (path / "__init__.py").is_file()
        )
    )


def _legacy_imports(root: Path, names: set[str]) -> tuple[str, ...]:
    prefixes = tuple(sorted(names, key=len, reverse=True))
    findings: list[str] = []
    for path in _python_files(root):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if any(
                re.match(
                    rf"\s*(from|import)\s+{re.escape(prefix)}(?:\.|\s|,|$)",
                    line,
                )
                for prefix in prefixes
            ):
                findings.append(f"{path.relative_to(root)}:{line_number}")
    return tuple(findings)


def _python_files(root: Path):
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative == Path("dev_tools/repository_layout.py"):
            continue
        if relative.parts and relative.parts[0] == "evidence":
            continue
        if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit",))
    parser.add_argument("--root-dir", default=".")
    args = parser.parse_args(argv)
    audit = audit_repository_layout(args.root_dir)
    print(json.dumps(asdict(audit), indent=2))
    return 0 if audit.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

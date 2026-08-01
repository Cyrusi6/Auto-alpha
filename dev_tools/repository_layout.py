"""Repository layout migration and regression checks.

The production tree has one public package, six domains, and a bounded set of
subsystems. Historical task packages and peer-level micro-packages are not
architectural boundaries and must not return.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import tokenize
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


PACKAGE_MAP = {
    "alpha_experiment_store": "auto_alpha.research.discovery.experiments",
    "alpha_factory": "auto_alpha.research.discovery.factory",
    "approval": "auto_alpha.platform.governance.approval",
    "artifact_schema": "auto_alpha.platform.artifacts.schema",
    "backfill_observer": "auto_alpha.data.ingestion.observer",
    "backfill_repair": "auto_alpha.data.ingestion.repair",
    "backtest": "auto_alpha.portfolio.simulation.backtest",
    "broker_adapter": "auto_alpha.execution.broker.adapter",
    "broker_connectivity": "auto_alpha.execution.broker.connectivity",
    "broker_file_gateway": "auto_alpha.execution.broker.file_gateway",
    "broker_mapping_certification": "auto_alpha.execution.broker.mapping",
    "broker_readonly_mirror": "auto_alpha.execution.broker.mirror",
    "broker_statement": "auto_alpha.execution.broker.statements",
    "broker_uat_lab": "auto_alpha.execution.broker.uat",
    "capacity_model": "auto_alpha.portfolio.simulation.capacity",
    "certification_campaign_store": "auto_alpha.validation.certification.campaigns",
    "ci": "auto_alpha.platform.governance.ci",
    "compute_cluster": "auto_alpha.platform.compute.scheduler",
    "corporate_actions": "auto_alpha.data.pit.corporate_actions",
    "cross_source_checks": "auto_alpha.data.quality.cross_source",
    "dashboard": "auto_alpha.platform.observability.dashboard",
    "data_backfill": "auto_alpha.data.ingestion.backfill",
    "data_lake": "auto_alpha.data.lake.store",
    "data_pipeline": "auto_alpha.data.ingestion.pipeline",
    "data_quality_lab": "auto_alpha.data.quality.lab",
    "data_source_validation": "auto_alpha.data.quality.source_validation",
    "evaluation": "auto_alpha.research.discovery.evaluation",
    "execution": "auto_alpha.execution.trading.engine",
    "execution_plan": "auto_alpha.execution.trading.plan",
    "experiment_orchestrator": "auto_alpha.research.discovery.orchestrator",
    "factor_certification": "auto_alpha.validation.certification.factors",
    "factor_engine": "auto_alpha.research.factors.engine",
    "factor_lifecycle": "auto_alpha.research.factors.lifecycle",
    "factor_store": "auto_alpha.research.factors.store",
    "feature_factory": "auto_alpha.research.features.factory",
    "feature_promotion": "auto_alpha.research.features.promotion",
    "formula_batch_eval": "auto_alpha.research.formulas.batch",
    "formula_corpus": "auto_alpha.research.formulas.corpus",
    "formula_search": "auto_alpha.research.formulas.search",
    "go_live_gate": "auto_alpha.execution.operations.go_live",
    "incident_response": "auto_alpha.execution.operations.incidents",
    "leakage_audit": "auto_alpha.validation.firewall.leakage",
    "matrix_refresh": "auto_alpha.data.matrix.refresh",
    "matrix_store": "auto_alpha.data.matrix.store",
    "model_core": "auto_alpha.research.formulas.runtime",
    "model_registry": "auto_alpha.research.factors.registry",
    "monitoring": "auto_alpha.platform.observability.monitoring",
    "neural_search": "auto_alpha.research.neural.search",
    "operations": "auto_alpha.execution.operations.daily",
    "operator_handoff": "auto_alpha.execution.operations.handoff",
    "paper_account": "auto_alpha.execution.trading.paper",
    "performance_benchmark": "auto_alpha.research.discovery.benchmark",
    "point_in_time": "auto_alpha.data.pit.engine",
    "portfolio_campaign_store": "auto_alpha.portfolio.construction.campaigns",
    "portfolio_certification": "auto_alpha.portfolio.construction.certification",
    "portfolio_lab": "auto_alpha.portfolio.construction.lab",
    "portfolio_optimizer": "auto_alpha.portfolio.construction.optimizer",
    "portfolio_research": "auto_alpha.portfolio.construction.research",
    "post_download_orchestrator": "auto_alpha.data.ingestion.post_download",
    "production_orchestrator": "auto_alpha.execution.operations.production",
    "production_replay": "auto_alpha.execution.operations.replay",
    "program_trading_compliance": "auto_alpha.execution.settlement.compliance",
    "raw_data_index": "auto_alpha.data.ingestion.index",
    "raw_data_landing": "auto_alpha.data.ingestion.landing",
    "real_data_ops": "auto_alpha.data.lake.operations",
    "reconciliation_center": "auto_alpha.execution.settlement.reconciliation",
    "release_manager": "auto_alpha.platform.governance.release",
    "research": "auto_alpha.research.discovery.studies",
    "research_data_readiness": "auto_alpha.data.pit.readiness",
    "research_firewall": "auto_alpha.validation.firewall.core",
    "research_suite": "auto_alpha.research.discovery.suite",
    "risk_controls": "auto_alpha.portfolio.risk.controls",
    "risk_model": "auto_alpha.portfolio.risk.model",
    "settlement_engine": "auto_alpha.execution.settlement.engine",
    "shadow_lab": "auto_alpha.execution.operations.shadow_lab",
    "shadow_trading": "auto_alpha.execution.trading.shadow",
    "strategy_manager": "auto_alpha.execution.trading.strategy",
    "universe": "auto_alpha.data.pit.universe",
    "validation_campaign_store": "auto_alpha.validation.lab.campaigns",
    "validation_lab": "auto_alpha.validation.lab.engine",
    "validation_red_team": "auto_alpha.validation.lab.red_team",
}


LIVE_READINESS_MAP = {
    "live_readiness.correctness_closure": "auto_alpha.platform.network_authority",
    "live_readiness.canary_authority": "auto_alpha.platform.network_authority._internal.application",
    "live_readiness.evidence_hardening": "auto_alpha.platform.network_authority._internal.evidence",
    "live_readiness.holdout_simulation": "auto_alpha.platform.network_authority._internal.simulation",
    "live_readiness.native_replay": "auto_alpha.platform.network_authority._internal.replay",
    "live_readiness.network_authorization": "auto_alpha.platform.network_authority._internal.authorization",
    "live_readiness.production_authority": "auto_alpha.platform.network_authority._internal.runtime",
    "live_readiness.production_hardening": "auto_alpha.platform.network_authority._internal.validation",
    "live_readiness.secure_acquisition": "auto_alpha.platform.network_authority._internal.acquisition",
    "live_readiness.source_salvage": "auto_alpha.platform.network_authority._internal.provenance",
    "live_readiness.valuation_remediation": "auto_alpha.platform.network_authority._internal.valuation",
    "live_readiness": "auto_alpha.platform.governance.readiness",
}

LIVE_READINESS_MISREWRITE_MAP = {
    old.replace("live_readiness", "auto_alpha.platform.governance.readiness", 1): new
    for old, new in LIVE_READINESS_MAP.items()
    if old != "live_readiness"
}


REMOVED_TASK_PACKAGES = tuple(f"task_{number}" for number in (
    "051_a", "052_a", "053_a", "054_a", "054_b", "054_c",
    "055_a", "055_b", "055_c", "055_d", "055_e", "055_f",
    "055_g", "055_h", "055_i", "055_j", "055_k",
))


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


def migrate_live_readiness(root_dir: str | Path) -> tuple[str, ...]:
    root = Path(root_dir).resolve()
    source = root / "src/live_readiness"
    if not source.is_dir():
        _ensure_layout_packages(root)
        return rewrite_module_references(
            root,
            LIVE_READINESS_MAP | LIVE_READINESS_MISREWRITE_MAP,
        )
    network = root / "src/auto_alpha/platform/network_authority"
    readiness = root / "src/auto_alpha/platform/governance/readiness"
    network.parent.mkdir(parents=True, exist_ok=True)
    readiness.parent.mkdir(parents=True, exist_ok=True)
    moves = {
        "correctness_closure": network,
        "canary_authority": network / "_internal/application",
        "evidence_hardening": network / "_internal/evidence",
        "holdout_simulation": network / "_internal/simulation",
        "native_replay": network / "_internal/replay",
        "network_authorization": network / "_internal/authorization",
        "production_authority": network / "_internal/runtime",
        "production_hardening": network / "_internal/validation",
        "secure_acquisition": network / "_internal/acquisition",
        "source_salvage": network / "_internal/provenance",
        "valuation_remediation": network / "_internal/valuation",
    }
    changed: list[str] = []
    for name, target in moves.items():
        item = source / name
        if item.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            item.rename(target)
            changed.append(str(target.relative_to(root)))
    readiness.mkdir(parents=True, exist_ok=True)
    for item in tuple(source.iterdir()):
        if item.name == "__pycache__":
            shutil.rmtree(item, ignore_errors=True)
        elif item.is_file():
            target = readiness / item.name
            item.rename(target)
            changed.append(str(target.relative_to(root)))
    if source.exists():
        source.rmdir()
    _ensure_layout_packages(root)
    changed.extend(
        rewrite_module_references(
            root,
            LIVE_READINESS_MAP | LIVE_READINESS_MISREWRITE_MAP,
        )
    )
    return tuple(sorted(set(changed)))


def migrate_domain_packages(root_dir: str | Path) -> tuple[str, ...]:
    root = Path(root_dir).resolve()
    source = root / "src"
    changed: list[str] = []
    for old, canonical in PACKAGE_MAP.items():
        origin = source / old
        if not origin.is_dir():
            continue
        target = source.joinpath(*canonical.split("."))
        target.parent.mkdir(parents=True, exist_ok=True)
        origin.rename(target)
        changed.append(str(target.relative_to(root)))
    _ensure_layout_packages(root)
    changed.extend(rewrite_module_references(root, PACKAGE_MAP))
    return tuple(sorted(set(changed)))


def rewrite_module_references(
    root_dir: str | Path,
    mapping: dict[str, str],
) -> tuple[str, ...]:
    root = Path(root_dir).resolve()
    changed: list[str] = []
    ordered = tuple(sorted(mapping, key=len, reverse=True))
    for path in _python_files(root):
        original = path.read_text(encoding="utf-8")
        updated = original
        for old in ordered:
            new = mapping[old]
            updated = re.sub(
                rf"(?m)^(\s*from\s+){re.escape(old)}(?=\.|\s+import\b)",
                rf"\1{new}",
                updated,
            )
            updated = re.sub(
                rf"(?m)^(\s*import\s+){re.escape(old)}(?=\.|\s+as\b|\s*$|,)",
                rf"\1{new}",
                updated,
            )
        updated = _rewrite_string_tokens(updated, mapping)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    return tuple(sorted(changed))


def _rewrite_string_tokens(source: str, mapping: dict[str, str]) -> str:
    tokens = []
    ordered = tuple(sorted(mapping, key=len, reverse=True))
    try:
        stream = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in stream:
            if token.type == tokenize.STRING:
                value = token.string
                for old in ordered:
                    new = mapping[old]
                    value = re.sub(
                        rf"(?<![A-Za-z0-9_]){re.escape(old)}(?=\.)",
                        new,
                        value,
                    )
                    value = value.replace(
                        f"src/{old.replace('.', '/')}/",
                        f"src/{new.replace('.', '/')}/",
                    )
                    value = re.sub(
                        rf"(?<=[\"']){re.escape(old.replace('.', '/'))}(?=/)",
                        new.replace(".", "/"),
                        value,
                    )
                token = tokenize.TokenInfo(
                    token.type, value, token.start, token.end, token.line
                )
            tokens.append(token)
    except (IndentationError, tokenize.TokenError):
        return source
    return tokenize.untokenize(tokens)


def _ensure_layout_packages(root: Path) -> None:
    source = root / "src/auto_alpha"
    source.mkdir(parents=True, exist_ok=True)
    packages = [source]
    for domain, subsystems in DOMAIN_SUBSYSTEMS.items():
        domain_path = source / domain
        packages.append(domain_path)
        packages.extend(domain_path / subsystem for subsystem in subsystems)
    packages.append(source / "platform/network_authority/_internal")
    for package in packages:
        package.mkdir(parents=True, exist_ok=True)
        init = package / "__init__.py"
        if not init.exists():
            label = package.relative_to(source).as_posix().replace("/", " ") or "platform"
            init.write_text(f'"""Auto-alpha {label} package."""\n', encoding="utf-8")


def audit_repository_layout(root_dir: str | Path) -> LayoutAudit:
    root = Path(root_dir).resolve()
    src = root / "src"
    legacy_names = set(PACKAGE_MAP) | {"live_readiness"} | set(REMOVED_TASK_PACKAGES)
    package_roots = (root, src)
    legacy_directories = tuple(
        sorted(
            str(path.relative_to(root))
            for package_root in package_roots
            if package_root.is_dir()
            for path in package_root.iterdir()
            if path.is_dir() and path.name in legacy_names
        )
    )
    import_prefixes = tuple(sorted(legacy_names, key=len, reverse=True))
    legacy_imports: list[str] = []
    for path in _python_files(root):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(
                re.match(rf"\s*(from|import)\s+{re.escape(prefix)}(?:\.|\s|,|$)", line)
                for prefix in import_prefixes
            ):
                legacy_imports.append(f"{path.relative_to(root)}:{line_number}")
    packaging = (root / "pyproject.toml").read_text(encoding="utf-8")
    legacy_packaging_entries = tuple(
        sorted(name for name in legacy_names if f'"{name}"' in packaging)
    )
    auto_alpha = src / "auto_alpha"
    domain_issues: list[str] = []
    actual_domains = tuple(
        sorted(path.name for path in auto_alpha.iterdir() if path.is_dir())
    ) if auto_alpha.is_dir() else ()
    if actual_domains != tuple(sorted(DOMAIN_SUBSYSTEMS)):
        domain_issues.append(f"domains:{actual_domains!r}")
    subsystem_count = 0
    for domain, expected in DOMAIN_SUBSYSTEMS.items():
        domain_path = auto_alpha / domain
        actual = tuple(sorted(path.name for path in domain_path.iterdir() if path.is_dir())) if domain_path.is_dir() else ()
        expected_sorted = tuple(sorted(expected))
        if actual != expected_sorted:
            domain_issues.append(f"subsystems:{domain}:{actual!r}")
        subsystem_count += len(actual)
    top_level_package_count = sum(
        1 for path in root.iterdir() if path.is_dir() and (path / "__init__.py").is_file()
    )
    source_package_count = sum(
        1 for path in src.iterdir() if path.is_dir() and (path / "__init__.py").is_file()
    )
    passed = not (
        legacy_directories
        or legacy_imports
        or legacy_packaging_entries
        or domain_issues
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
        legacy_imports=tuple(legacy_imports),
        legacy_packaging_entries=legacy_packaging_entries,
        domain_issues=tuple(domain_issues),
    )


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
    parser.add_argument(
        "command",
        choices=("audit", "migrate-live", "migrate-domains"),
    )
    parser.add_argument("--root-dir", default=".")
    args = parser.parse_args(argv)
    if args.command == "migrate-live":
        changed = migrate_live_readiness(args.root_dir)
        print(json.dumps({"changed_count": len(changed), "changed": changed}, indent=2))
        return 0
    if args.command == "migrate-domains":
        changed = migrate_domain_packages(args.root_dir)
        print(json.dumps({"changed_count": len(changed), "changed": changed}, indent=2))
        return 0
    audit = audit_repository_layout(args.root_dir)
    print(json.dumps(asdict(audit), indent=2))
    return 0 if audit.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

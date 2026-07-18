"""Release dependency, module and CLI inventories."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from .models import CliInventory, DependencyInventory, ModuleInventory


PLATFORM_MODULES = [
    "approval",
    "alpha_experiment_store",
    "alpha_factory",
    "backfill_repair",
    "backfill_observer",
    "backtest",
    "broker_adapter",
    "broker_connectivity",
    "broker_file_gateway",
    "broker_mapping_certification",
    "broker_statement",
    "broker_readonly_mirror",
    "broker_uat_lab",
    "capacity_model",
    "certification_campaign_store",
    "corporate_actions",
    "compute_cluster",
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
    "factor_engine",
    "factor_store",
    "factor_lifecycle",
    "factor_certification",
    "feature_factory",
    "feature_promotion",
    "formula_batch_eval",
    "formula_corpus",
    "formula_search",
    "go_live_gate",
    "incident_response",
    "matrix_store",
    "matrix_refresh",
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
    "post_download_orchestrator",
    "program_trading_compliance",
    "production_orchestrator",
    "production_replay",
    "reconciliation_center",
    "raw_data_landing",
    "raw_data_index",
    "real_data_ops",
    "research",
    "research_data_readiness",
    "research_suite",
    "risk_model",
    "risk_controls",
    "settlement_engine",
    "shadow_lab",
    "shadow_trading",
    "leakage_audit",
    "strategy_manager",
    "task_055_j",
    "universe",
    "validation_campaign_store",
    "validation_lab",
    "live_readiness",
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
        path = root / name
        modules.append(
            {
                "module": name,
                "path": str(path),
                "is_package": (path / "__init__.py").exists(),
                "exists": path.exists(),
                "included": path.exists() and (path / "__init__.py").exists(),
            }
        )
    return ModuleInventory(modules=modules)


def build_cli_inventory(root_dir: str | Path = ".") -> CliInventory:
    root = Path(root_dir)
    entries = []
    for module in PLATFORM_MODULES:
        package_dir = root / module
        if not package_dir.exists():
            continue
        for path in sorted(package_dir.glob("*.py")):
            if path.name.startswith("run_") or path.name in {"runner.py", "engine.py"}:
                text = path.read_text(encoding="utf-8")
                if "def main(" not in text:
                    continue
                entries.append(
                    {
                        "module": f"{module}.{path.stem}",
                        "path": str(path),
                        "has_main": True,
                    }
                )
    return CliInventory(entries=entries)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

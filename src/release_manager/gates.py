"""Local release gate checks."""

from __future__ import annotations

import importlib
import os
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from artifact_schema.report import build_validation_report, write_validation_report
from artifact_schema.scanner import paths_from_artifact_catalog, scan_artifact_dirs
from artifact_schema.validator import validate_artifact

from .inventory import PLATFORM_MODULES
from .models import ReleaseConfig, ReleaseGateCheck, ReleaseGateReport


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def run_release_gates(config: ReleaseConfig, root_dir: str | Path = ".") -> ReleaseGateReport:
    root = Path(root_dir)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checks: list[ReleaseGateCheck] = []
    checks.append(_timed("git_clean_check", lambda: _git_clean_check(root)))
    checks.append(_timed("no_real_network_by_default", lambda: _network_check(config)))
    checks.append(_timed("token_redaction_check", lambda: _token_redaction_check(output_dir)))
    checks.append(_timed("legacy_business_term_scan", lambda: _legacy_business_term_scan(root)))
    if config.run_import_smoke:
        checks.append(_timed("import_smoke", lambda: _import_smoke()))
    if config.run_dashboard_import:
        checks.append(_timed("dashboard_import_smoke", lambda: _dashboard_import_smoke()))
    if config.run_schema_validation:
        checks.append(_timed("artifact_schema_validation", lambda: _schema_validation(config, output_dir)))
    if config.run_build:
        checks.append(_timed("package_build", lambda: _run_command(["uv", "build"], root)))
    if config.run_tests:
        command = ["uv", "run", "pytest", *shlex.split(config.pytest_args or "")]
        checks.append(_timed("pytest", lambda: _run_command(command, root)))
    status = "failed" if any(check.status == "error" for check in checks) else "passed"
    return ReleaseGateReport(
        release_name=config.release_name,
        created_at=utc_now(),
        status=status,
        checks=checks,
    )


def _timed(name: str, fn: Callable[[], tuple[str, str, dict]]) -> ReleaseGateCheck:
    started = time.perf_counter()
    started_at = utc_now()
    try:
        status, message, metadata = fn()
    except Exception as exc:  # pragma: no cover - defensive release reporting
        status, message, metadata = "error", str(exc), {"exception_type": type(exc).__name__}
    finished_at = utc_now()
    return ReleaseGateCheck(
        name=name,
        status=status,
        message=message,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=float(time.perf_counter() - started),
        metadata=metadata,
    )


def _git_clean_check(root: Path) -> tuple[str, str, dict]:
    result = subprocess.run(["git", "status", "--short"], cwd=root, text=True, capture_output=True, check=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    status = "passed" if not lines else "warning"
    return status, "worktree clean" if not lines else "worktree has local changes", {"changed_files": lines[:200]}


def _network_check(config: ReleaseConfig) -> tuple[str, str, dict]:
    if config.allow_network:
        return "warning", "network was explicitly allowed for this release run", {"allow_network": True}
    return "passed", "network disabled by default", {"allow_network": False}


def _token_redaction_check(output_dir: Path) -> tuple[str, str, dict]:
    sensitive = os.environ.get("TUSHARE_TOKEN")
    if not sensitive:
        return "passed", "no Tushare token present in environment", {}
    leaked_paths: list[str] = []
    if output_dir.exists():
        for path in output_dir.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".txt"}:
                try:
                    if sensitive in path.read_text(encoding="utf-8", errors="ignore"):
                        leaked_paths.append(str(path))
                except OSError:
                    continue
    status = "error" if leaked_paths else "passed"
    return status, "token leak detected" if leaked_paths else "token not present in release artifacts", {"leaked_paths": leaked_paths}


def _legacy_business_term_scan(root: Path) -> tuple[str, str, dict]:
    terms = ["cr" + "ypto", "so" + "lana", "me" + "me"]
    paths = [
        root / "artifact_schema",
        root / "release_manager",
        root / "ci",
        root / ".github" / "workflows",
    ]
    hits: list[dict] = []
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".yml", ".yaml", ".md", ".toml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for term in terms:
                if term in text:
                    hits.append({"path": str(path), "term": term})
    return ("error" if hits else "passed", "legacy terms found" if hits else "no legacy terms found", {"hits": hits})


def _import_smoke() -> tuple[str, str, dict]:
    imported = []
    failures = []
    for module in PLATFORM_MODULES:
        try:
            importlib.import_module(module)
            imported.append(module)
        except Exception as exc:  # pragma: no cover - module import diagnostics
            failures.append({"module": module, "error": str(exc)})
    return ("error" if failures else "passed", "import smoke complete", {"imported": imported, "failures": failures})


def _dashboard_import_smoke() -> tuple[str, str, dict]:
    importlib.import_module("dashboard.app")
    return "passed", "dashboard.app imported", {}


def _schema_validation(config: ReleaseConfig, output_dir: Path) -> tuple[str, str, dict]:
    paths = []
    paths.extend(scan_artifact_dirs(config.artifact_dirs))
    for catalog in config.artifact_catalog_paths:
        paths.extend(paths_from_artifact_catalog(catalog))
    paths = sorted(set(Path(path) for path in paths))
    results = [validate_artifact(path) for path in paths]
    report = build_validation_report(results)
    json_path, md_path, issues_path = write_validation_report(report, output_dir)
    status = "error" if report.error_count else ("warning" if report.warning_count else "passed")
    return status, "artifact schema validation complete", {
        "artifact_count": len(results),
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "artifact_validation_report_path": str(json_path),
        "artifact_validation_report_md_path": str(md_path),
        "artifact_validation_issues_path": str(issues_path),
    }


def _run_command(command: list[str], root: Path) -> tuple[str, str, dict]:
    started = time.perf_counter()
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    metadata = {
        "command": command,
        "returncode": result.returncode,
        "duration_seconds": float(time.perf_counter() - started),
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }
    return ("passed" if result.returncode == 0 else "error", "command completed", metadata)

"""Low-cost secret scanning for local artifacts."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .models import SecretScanFinding, SecretScanReport


SECRET_KEY_RE = re.compile(r"(?i)(tushare_token|token|password|secret|api_key|private_key|broker_password|broker_token)\s*[:=]\s*([^\s,'\"]+)")
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
SCAN_SUFFIXES = {".json", ".jsonl", ".csv", ".md", ".txt", ".py", ".yaml", ".yml", ".toml", ".env", ""}
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist"}
PLACEHOLDERS = {"", "changeme", "placeholder", "none", "null", "<real_token>", "<token>", "your_token", "0"}


def scan_artifacts_for_secrets(paths: list[str | Path]) -> SecretScanReport:
    findings: list[SecretScanFinding] = []
    scanned = 0
    for file_path in _iter_files(paths):
        scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in SECRET_KEY_RE.finditer(line):
                key = match.group(1)
                value = match.group(2).strip().strip("'\"")
                severity = "info" if _is_allowed_placeholder(file_path, value) else "blocker"
                findings.append(
                    SecretScanFinding(
                        path=str(file_path),
                        line=line_no,
                        severity=severity,
                        code="explicit_secret" if severity == "blocker" else "placeholder_secret",
                        message=f"{key} assignment found",
                        excerpt=_redact(line),
                    )
                )
            if LONG_TOKEN_RE.search(line) and "sha256" not in line.lower() and "hash" not in line.lower():
                findings.append(
                    SecretScanFinding(
                        path=str(file_path),
                        line=line_no,
                        severity="warning",
                        code="long_token_like_string",
                        message="long token-like string found",
                        excerpt=_redact(line),
                    )
                )
    blockers = sum(1 for item in findings if item.severity == "blocker")
    warnings = sum(1 for item in findings if item.severity == "warning")
    return SecretScanReport(
        created_at=_utc_now(),
        scanned_files=scanned,
        finding_count=len(findings),
        blocker_count=blockers,
        warning_count=warnings,
        findings=findings,
        status="failed" if blockers else "warning" if warnings else "complete",
    )


def _iter_files(paths: list[str | Path]):
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            continue
        if root.is_file():
            if _should_scan(root):
                yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and _should_scan(path):
                yield path


def _should_scan(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    return path.suffix.lower() in SCAN_SUFFIXES


def _is_allowed_placeholder(path: Path, value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return path.name == ".env.example" and normalized in PLACEHOLDERS


def _redact(line: str) -> str:
    return SECRET_KEY_RE.sub(lambda match: f"{match.group(1)}=<redacted>", line)[:240]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

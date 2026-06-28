"""Static leakage checks for formula tokens and formula metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from model_core.vocab import FORMULA_VOCAB
from model_core.vm import StackVM

from .models import FormulaLeakageScanResult, LeakageIssue


FORBIDDEN_PATTERNS = ("TARGET_RET", "FUTURE", "LEAD", "SHIFT_NEGATIVE", "FORWARD")


def scan_formula_leakage(formulas: Iterable[dict] | None = None, formula_paths: Iterable[str | Path] | None = None) -> FormulaLeakageScanResult:
    items = list(formulas or [])
    for path in formula_paths or []:
        items.extend(_read_formulas(path))
    if not items:
        items = [{"name": name, "formula_tokens": [FORMULA_VOCAB.encode_name(name)]} for name in FORMULA_VOCAB.feature_names[:5]]
    issues: list[LeakageIssue] = []
    blocked = 0
    warnings = 0
    future_token_count = sum(1 for name in FORMULA_VOCAB.token_names if any(pattern in name.upper() for pattern in FORBIDDEN_PATTERNS))
    vm = StackVM()
    for idx, item in enumerate(items):
        tokens = item.get("formula_tokens") or item.get("tokens") or []
        names = item.get("formula_names") or []
        try:
            if not names:
                names = FORMULA_VOCAB.decode_tokens([int(token) for token in tokens])
        except Exception:
            names = [str(token) for token in tokens]
        forbidden = [name for name in names if any(pattern in str(name).upper() for pattern in FORBIDDEN_PATTERNS)]
        if forbidden:
            blocked += 1
            issues.append(LeakageIssue("blocker", "forbidden_future_token", "formula contains forward-looking token", "formula", str(idx), {"tokens": forbidden}))
        try:
            valid, reason = vm.validate_with_reason([int(token) for token in tokens])
        except Exception as exc:
            valid, reason = False, str(exc)
        if not valid:
            warnings += 1
            issues.append(LeakageIssue("warning", "invalid_formula", reason, "formula", str(idx), {"formula_names": names}))
    return FormulaLeakageScanResult(
        scanned_formula_count=len(items),
        blocked_formula_count=blocked,
        warning_formula_count=warnings,
        supported_future_token_count=future_token_count,
        issues=issues,
    )


def _read_formulas(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    if target.suffix == ".jsonl":
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "formulas", "records"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        return [payload]
    return []

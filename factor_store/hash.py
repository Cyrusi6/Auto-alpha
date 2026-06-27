"""Stable identifiers for factors and experiments."""

from __future__ import annotations

import hashlib
import json


def stable_formula_hash(
    formula_tokens: list[int],
    formula_names: list[str],
    feature_version: str,
    operator_version: str,
) -> str:
    payload = {
        "feature_version": feature_version,
        "formula_names": formula_names,
        "formula_tokens": formula_tokens,
        "operator_version": operator_version,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def make_factor_id(formula_hash: str) -> str:
    return f"factor_{formula_hash[:16]}"


def make_experiment_id(factor_id: str, created_at: str) -> str:
    safe_timestamp = "".join(char if char.isalnum() else "_" for char in created_at).strip("_")
    suffix = factor_id.removeprefix("factor_")
    return f"exp_{suffix}_{safe_timestamp}"

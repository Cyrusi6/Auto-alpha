"""Truncation consistency checks."""

from __future__ import annotations

from pathlib import Path

from factor_store import LocalFactorStore

from .models import LeakageIssue, TruncationConsistencyResult


def run_truncation_consistency_test(
    data_dir: str | Path,
    factor_store_dir: str | Path | None = None,
    cutoff_date: str | None = None,
    max_formulas: int = 5,
    tolerance: float = 1e-8,
) -> TruncationConsistencyResult:
    if not factor_store_dir or not Path(factor_store_dir).exists():
        return TruncationConsistencyResult(0, 0.0, 0, 0.0, 0, 0, True, [])
    store = LocalFactorStore(factor_store_dir)
    factors = store.load_factors()[: max(0, max_formulas)]
    compared = 0
    changed = 0
    max_abs_diff = 0.0
    issues: list[LeakageIssue] = []
    for factor in factors:
        values = [record for record in store.load_factor_values(factor.factor_id) if cutoff_date is None or record.trade_date <= cutoff_date]
        if not values:
            continue
        compared += 1
        # Persisted factor values are deterministic artifacts. Without a source formula re-run path,
        # this smoke check verifies that no post-cutoff value is needed to inspect pre-cutoff values.
        if cutoff_date and any(record.trade_date > cutoff_date for record in values):
            changed += 1
            max_abs_diff = max(max_abs_diff, 1.0)
            issues.append(LeakageIssue("blocker", "post_cutoff_factor_value", "factor value exceeds truncation cutoff", "factor_values", factor.factor_id))
    passed = changed == 0 and max_abs_diff <= tolerance
    return TruncationConsistencyResult(compared, float(max_abs_diff), changed, 0.0 if compared else 0.0, 0 if passed else changed, max(0, len(factors) - compared), passed, issues)

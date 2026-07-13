"""Deterministic validation split builders."""

from __future__ import annotations

import itertools
import random

from .models import ValidationSplit, ValidationSplitMethod


def build_simple_walk_forward_splits(dates: list[str], train_size: int, test_size: int, step_size: int) -> list[ValidationSplit]:
    dates = sorted(dates)
    train_size = max(1, int(train_size))
    test_size = max(1, int(test_size))
    step_size = max(1, int(step_size))
    if len(dates) <= train_size:
        return [_degraded_split("simple_walk_forward_0", ValidationSplitMethod.simple_walk_forward, dates)]
    splits = []
    cursor = train_size
    idx = 0
    while cursor < len(dates):
        test = dates[cursor : min(cursor + test_size, len(dates))]
        if not test:
            break
        splits.append(
            ValidationSplit(
                split_id=f"simple_walk_forward_{idx}",
                method=ValidationSplitMethod.simple_walk_forward,
                train_dates=dates[:cursor],
                validation_dates=[],
                test_dates=test,
            )
        )
        cursor += step_size
        idx += 1
    return splits or [_degraded_split("simple_walk_forward_0", ValidationSplitMethod.simple_walk_forward, dates)]


def build_rolling_walk_forward_splits(
    dates: list[str],
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int,
    embargo_size: int = 0,
) -> list[ValidationSplit]:
    dates = sorted(dates)
    train_size = max(1, int(train_size))
    validation_size = max(0, int(validation_size))
    test_size = max(1, int(test_size))
    step_size = max(1, int(step_size))
    embargo_size = max(0, int(embargo_size))
    total = train_size + validation_size + test_size + embargo_size * 2
    if len(dates) < total:
        return build_simple_walk_forward_splits(dates, max(1, min(train_size, len(dates) - 1)), test_size, step_size)
    splits = []
    start = 0
    idx = 0
    while start + total <= len(dates):
        train_end = start + train_size
        valid_start = train_end + embargo_size
        valid_end = valid_start + validation_size
        test_start = valid_end + embargo_size
        splits.append(
            ValidationSplit(
                split_id=f"rolling_walk_forward_{idx}",
                method=ValidationSplitMethod.rolling_walk_forward,
                train_dates=dates[start:train_end],
                validation_dates=dates[valid_start:valid_end],
                test_dates=dates[test_start : test_start + test_size],
                embargo_dates=dates[train_end:valid_start] + dates[valid_end:test_start],
                metadata={"embargo_size": embargo_size},
            )
        )
        start += step_size
        idx += 1
    return splits


def build_anchored_walk_forward_splits(dates: list[str], min_train_size: int, test_size: int, step_size: int) -> list[ValidationSplit]:
    return build_simple_walk_forward_splits(dates, min_train_size, test_size, step_size)


def build_purged_embargo_splits(dates: list[str], n_splits: int, embargo_size: int) -> list[ValidationSplit]:
    dates = sorted(dates)
    n_splits = max(1, int(n_splits))
    embargo_size = max(0, int(embargo_size))
    if len(dates) < 3:
        return [_degraded_split("purged_embargo_0", ValidationSplitMethod.purged_embargo, dates)]
    fold_size = max(1, len(dates) // n_splits)
    splits = []
    for idx in range(n_splits):
        test_start = idx * fold_size
        test_end = len(dates) if idx == n_splits - 1 else min(len(dates), test_start + fold_size)
        test = dates[test_start:test_end]
        embargo_start = max(0, test_start - embargo_size)
        embargo_end = min(len(dates), test_end + embargo_size)
        embargo = [d for d in dates[embargo_start:embargo_end] if d not in test]
        train = [d for d in dates if d not in test and d not in embargo]
        if not test or not train:
            continue
        splits.append(
            ValidationSplit(
                split_id=f"purged_embargo_{idx}",
                method=ValidationSplitMethod.purged_embargo,
                train_dates=train,
                validation_dates=[],
                test_dates=test,
                embargo_dates=embargo,
                metadata={"embargo_size": embargo_size},
            )
        )
    return splits or [_degraded_split("purged_embargo_0", ValidationSplitMethod.purged_embargo, dates)]


def build_cscv_splits(dates: list[str], n_groups: int, max_combinations: int) -> list[ValidationSplit]:
    dates = sorted(dates)
    n_groups = max(2, int(n_groups))
    max_combinations = max(1, int(max_combinations))
    if len(dates) < n_groups:
        return [_degraded_split("cscv_0", ValidationSplitMethod.cscv, dates)]
    groups = [dates[idx::n_groups] for idx in range(n_groups)]
    combos = list(itertools.combinations(range(n_groups), max(1, n_groups // 2)))[:max_combinations]
    splits = []
    for idx, combo in enumerate(combos):
        test = sorted(d for group_idx in combo for d in groups[group_idx])
        train = sorted(d for group_idx, group in enumerate(groups) if group_idx not in combo for d in group)
        if train and test:
            splits.append(
                ValidationSplit(
                    split_id=f"cscv_{idx}",
                    method=ValidationSplitMethod.cscv,
                    train_dates=train,
                    validation_dates=[],
                    test_dates=test,
                    metadata={"test_group_ids": list(combo)},
                )
            )
    return splits or [_degraded_split("cscv_0", ValidationSplitMethod.cscv, dates)]


def build_time_block_bootstrap_splits(dates: list[str], block_size: int, n_samples: int, seed: int) -> list[ValidationSplit]:
    dates = sorted(dates)
    block_size = max(1, int(block_size))
    n_samples = max(1, int(n_samples))
    rng = random.Random(seed)
    if len(dates) <= block_size:
        return [_degraded_split("time_block_bootstrap_0", ValidationSplitMethod.time_block_bootstrap, dates)]
    blocks = [dates[i : i + block_size] for i in range(0, len(dates), block_size)]
    splits = []
    for idx in range(n_samples):
        test_block = rng.randrange(len(blocks))
        test = list(blocks[test_block])
        train = [d for block_idx, block in enumerate(blocks) if block_idx != test_block for d in block]
        splits.append(
            ValidationSplit(
                split_id=f"time_block_bootstrap_{idx}",
                method=ValidationSplitMethod.time_block_bootstrap,
                train_dates=train,
                validation_dates=[],
                test_dates=test,
                metadata={"test_block": test_block},
            )
        )
    return splits


def build_splits(
    method: str,
    dates: list[str],
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int,
    embargo_size: int,
    cscv_groups: int,
    max_cscv_combinations: int,
) -> list[ValidationSplit]:
    if method == ValidationSplitMethod.rolling_walk_forward:
        return build_rolling_walk_forward_splits(dates, train_size, validation_size, test_size, step_size, embargo_size)
    if method == ValidationSplitMethod.anchored_walk_forward:
        return build_anchored_walk_forward_splits(dates, train_size, test_size, step_size)
    if method == ValidationSplitMethod.purged_embargo:
        return build_purged_embargo_splits(dates, max(2, cscv_groups), embargo_size)
    if method == ValidationSplitMethod.cscv:
        return build_cscv_splits(dates, cscv_groups, max_cscv_combinations)
    if method == ValidationSplitMethod.time_block_bootstrap:
        return build_time_block_bootstrap_splits(dates, test_size, max_cscv_combinations, seed=17)
    return build_simple_walk_forward_splits(dates, train_size, test_size, step_size)


def _degraded_split(split_id: str, method: str, dates: list[str]) -> ValidationSplit:
    train = dates[:-1] if len(dates) > 1 else list(dates)
    test = dates[-1:] if dates else []
    return ValidationSplit(
        split_id=split_id,
        method=method,
        train_dates=train,
        validation_dates=[],
        test_dates=test,
        metadata={"warning": "insufficient dates; degraded split used"},
    )
